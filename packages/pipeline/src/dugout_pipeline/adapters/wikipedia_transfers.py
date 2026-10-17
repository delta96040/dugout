"""Transfers since the transfermarkt-datasets snapshot (July 2026), from Wikipedia's transfer lists.

Pages "List of <country> football transfers <window>" (CC BY-SA 4.0; config/transfer_lists.yaml) are read as
wikitext from `/wiki/<title>?action=raw`, which robots.txt allows (the `/w/` API path is disallowed, so we do not
use it). Two layouts exist:

  table     one row per transfer: date | player | moving from | moving to | fee (England, Italy, Scotland, ...),
            plus a loans table with start and end dates
  by club   a section per club with an "In" and an "Out" list of {{fs player}} lines whose note says
            "from X", "on loan to Y", "loan return to Z" ... (Spain, Germany, France, ...): no fees; the date is the
            cited announcement's date when the line has one

Players and clubs are linked articles; Wikidata maps the articles to Transfermarkt ids (P2446 players, P7223
clubs) so the rows join the rest of the warehouse. Fees are converted to euros with the ECB reference rate of the
transfer date (ECB statistical data, free reuse with attribution). Rows are facts (who moved where, when, for
what reported fee); the pages' prose is not stored.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import quote_plus

import yaml

from dugout_pipeline.adapters.base import DAY, TIER_DATA, SourceAdapter
from dugout_pipeline.db import provenance_sink, upsert
from dugout_pipeline.http import CachedClient, FetchError

log = logging.getLogger(__name__)

WIKI = "https://en.wikipedia.org/wiki/"
WIKIDATA = "https://query.wikidata.org/sparql"
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/"
SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD"}
NO_CLUB = {"free agent", "unattached", "retired", "released", "without club", "none", "n/a"}
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}

# -- wikitext helpers ---------------------------------------------------------------------------------------------


def strip_refs(s: str) -> str:
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>/]*/>", "", s)
    return re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)


def split_top(s: str, sep: str = "|") -> list[str]:
    """Split on `sep` outside [[...]] and {{...}}."""
    out, depth, cur, i = [], 0, [], 0
    while i < len(s):
        two = s[i : i + 2]
        if two in ("[[", "{{"):
            depth += 1
            cur.append(two)
            i += 2
            continue
        if two in ("]]", "}}") and depth:
            depth -= 1
            cur.append(two)
            i += 2
            continue
        if depth == 0 and s.startswith(sep, i):
            out.append("".join(cur))
            cur = []
            i += len(sep)
            continue
        cur.append(s[i])
        i += 1
    out.append("".join(cur))
    return out


def norm_title(t: str) -> str:
    t = t.split("#")[0].replace("_", " ").strip()
    return t[:1].upper() + t[1:] if t else t


def first_link(s: str) -> tuple[str | None, str | None]:
    """(article title, shown text) of the first [[link]] (files and categories skipped)."""
    for m in re.finditer(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", s):
        target = m.group(1)
        if ":" in target.split("#")[0][:12]:
            continue
        return norm_title(target), (m.group(2) or target).strip()
    return None, None


def _params(tpl_body: str) -> tuple[list[str], dict[str, str]]:
    pos, named = [], {}
    for part in split_top(tpl_body)[1:]:
        k, eq, v = part.partition("=")
        if eq and re.fullmatch(r"\s*[\w ]+\s*", k):
            named[k.strip()] = v.strip()
        else:
            pos.append(part.strip())
    return pos, named


def _templates(s: str):
    """Top-level {{...}} spans as (start, end, body)."""
    i, depth, start = 0, 0, 0
    while i < len(s) - 1:
        if s[i : i + 2] == "{{":
            if depth == 0:
                start = i
            depth += 1
            i += 2
        elif s[i : i + 2] == "}}" and depth:
            depth -= 1
            i += 2
            if depth == 0:
                yield start, i, s[start + 2 : i - 2]
        else:
            i += 1


def player_cell(cell: str) -> tuple[str | None, str | None]:
    """(name, article title) from a player cell: {{sortname|First|Last|...}}, {{Sort|key|[[X]]}} or [[X]]."""
    for _, _, body in _templates(cell):
        name = body.split("|", 1)[0].strip().lower()
        if name == "sortname":
            pos, named = _params(body)
            if len(pos) < 2:
                continue
            full = f"{pos[0]} {pos[1]}".strip()
            if named.get("nolink"):
                return full, None
            target = pos[2] if len(pos) > 2 and pos[2] else full
            if named.get("dab"):
                target = f"{target} ({named['dab']})"
            return full, norm_title(target)
        if name == "sort":
            pos, _ = _params(body)
            if len(pos) >= 2:
                title, shown = first_link(pos[1])
                return (shown or plain(pos[1])), title
    title, shown = first_link(cell)
    return (shown or plain(cell) or None), title


def plain(s: str) -> str:
    s = strip_refs(s)
    for _ in range(3):
        s = re.sub(r"\{\{(?:flag\w*|ntsh|nts)\|[^{}]*\}\}", "", s, flags=re.I)
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s: str) -> date | None:
    m = re.search(r"\{\{\s*dts\s*\|([^{}]*)\}\}", s, re.I)
    if m:
        nums = [p.strip() for p in m.group(1).split("|") if p.strip().isdigit()]
        if len(nums) >= 3:
            try:
                return date(int(nums[0]), int(nums[1]), int(nums[2]))
            except ValueError:
                return None
    t = plain(s)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", t) or re.search(
        r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", t
    )
    if m:
        a, b, y = m.groups()
        day, mon = (a, b) if a.isdigit() else (b, a)
        if mon.lower() in MONTHS:
            try:
                return date(int(y), MONTHS[mon.lower()], int(day))
            except ValueError:
                return None
    return None


def parse_fee(cell: str, default_currency: str) -> dict[str, Any]:
    """Fee text, native amount and currency, and kind (free / loan / permanent)."""
    raw = strip_refs(cell)
    sort_val = None
    m = re.search(r"\{\{\s*ntsh\s*\|\s*([\d.]+)\s*\}\}", raw, re.I)
    if m:
        sort_val = float(m.group(1))
    text = plain(raw)
    low = text.lower()
    kind = "permanent"
    if low.startswith("free") or "free transfer" in low:
        kind = "free"
    elif low.startswith("loan") or "on loan" in low:
        kind = "loan"
    amount, currency = None, default_currency
    sym = re.search(r"(R\$|[£€$])\s*([\d.,]+)\s*(bn|billion|m|million|k|thousand)?", text, re.I)
    if sym:
        currency = SYMBOLS.get(sym.group(1), sym.group(1))
        num, mult = sym.group(2), (sym.group(3) or "").lower()
        if "," in num and mult and re.fullmatch(r"\d+,\d{1,2}", num):
            num = num.replace(",", ".")  # "€16,4M"
        num = num.replace(",", "")
        try:
            scale = {"": 1, "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get(mult, 1e9)
            amount = float(round(float(num) * scale))
        except ValueError:
            amount = None
    if amount is None and sort_val and sort_val >= 1000 and sym:
        amount = sort_val
    if amount is not None and sort_val and sort_val >= 1000 and abs(sort_val - amount) / amount < 0.2:
        amount = sort_val  # the sort key carries the exact reported figure
    return {
        "fee_text": text[:60] or None,
        "fee_native": amount if kind == "permanent" else (0.0 if kind == "free" else None),
        "currency": currency if amount is not None else None,
        "kind": kind,
    }


# -- table layout -------------------------------------------------------------------------------------------------


def _cells(line: str) -> list[str]:
    body = line[1:]
    out = []
    for c in split_top(body, "||") if "||" in body else [body]:
        parts = split_top(c, "|")
        # `rowspan="2" | 9 February 2026`: the part before a top-level pipe is attributes
        if len(parts) > 1 and "=" in parts[0] and "[[" not in parts[0] and "{{" not in parts[0]:
            out.append(("|".join(parts[1:]), _rowspan(parts[0])))
        else:
            out.append((c, 1))
    return out


def _rowspan(attrs: str) -> int:
    m = re.search(r"rowspan\s*=\s*\"?(\d+)", attrs)
    return int(m.group(1)) if m else 1


def _expand(row: list[tuple[str, int]], width: int, carry: dict[int, tuple[str, int]]) -> list[str]:
    """One table row's cells, filling columns still covered by an earlier cell's rowspan (`carry`, updated)."""
    cells, k = [], 0
    for j in range(width):
        if j in carry:
            val, left = carry[j]
            cells.append(val)
            if left > 1:
                carry[j] = (val, left - 1)
            else:
                del carry[j]
        elif k < len(row):
            val, span = row[k]
            k += 1
            cells.append(val)
            if span > 1:
                carry[j] = (val, span - 1)
        else:
            cells.append("")
    return cells


def tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Wikitables as (lower-case headers, rows of raw cells), with rowspans expanded."""
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("{|"):
            i += 1
            continue
        headers: list[str] = []
        rows: list[list[str]] = []
        row: list[tuple[str, int]] | None = None
        carry: dict[int, tuple[str, int]] = {}
        i += 1
        while i < len(lines) and not lines[i].lstrip().startswith("|}"):
            ln = lines[i].strip()
            if ln.startswith("!"):
                headers += [plain(h).lower() for h in split_top(ln[1:], "!!")]
            elif ln.startswith("|-"):
                if row is not None and headers:
                    rows.append(_expand(row, len(headers), carry))
                row = []
            elif ln.startswith("|+"):
                pass
            elif ln.startswith("|"):
                if row is None:
                    row = []
                row += _cells(ln)
            elif row:  # a cell continued on the next line
                last, span = row[-1]
                row[-1] = (last + "\n" + ln, span)
            i += 1
        if row and headers:
            rows.append(_expand(row, len(headers), carry))
        out.append((headers, rows))
        i += 1
    return out


def _col(headers: list[str], *names: str) -> int | None:
    for n in names:
        for j, h in enumerate(headers):
            if h == n or h.startswith(n):
                return j
    return None


def parse_tables(text: str, page: str, currency: str) -> list[dict[str, Any]]:
    out = []
    for headers, rows in tables(text):
        jf, jt = _col(headers, "moving from", "from"), _col(headers, "moving to", "to")
        jp = _col(headers, "player", "name")
        if jf is None or jt is None or jp is None:
            continue
        jd = _col(headers, "date", "start date")
        je = _col(headers, "end date")
        jfee = _col(headers, "fee")
        loans = jfee is None or _col(headers, "start date") is not None
        for cells in rows:
            name, title = player_cell(cells[jp])
            if not name:
                continue
            ft, fn = first_link(cells[jf])
            tt, tn = first_link(cells[jt])
            fee = (
                parse_fee(cells[jfee], currency)
                if jfee is not None and not loans
                else {"kind": "loan", "fee_text": None, "fee_native": None, "currency": None}
            )
            fn, tn = fn or plain(cells[jf]) or None, tn or plain(cells[jt]) or None
            if fn and fn.lower() in NO_CLUB:
                fn = ft = None
            if tn and tn.lower() in NO_CLUB:
                tn = tt = None
                fee["kind"] = "released"
            out.append(
                {
                    "page": page,
                    "date": parse_date(cells[jd]) if jd is not None else None,
                    "player_name": name,
                    "player_title": title,
                    "from_club": fn,
                    "from_title": ft,
                    "to_club": tn,
                    "to_title": tt,
                    "loan_end": parse_date(cells[je]) if je is not None else None,
                    **fee,
                }
            )
    return out


# -- by-club layout -----------------------------------------------------------------------------------------------

_KIND = [
    (r"^on loan\b", "loan"),
    (r"^(?:loan return|return(?:ed)? from loan|end of loan)\b", "end_of_loan"),
    (r"^(?:from|to)\b", "permanent"),
    (
        r"^(?:free agent|released|retired|fired|contract (?:terminated|expired)|end of contract|unattached)",
        "free",
    ),
]


def parse_by_club(text: str, page: str) -> list[dict[str, Any]]:
    """The first {{fs start}} list under a club heading is its arrivals, the second its departures."""
    out = []
    club_title = club_name = None
    lists_seen = 0
    for line in text.splitlines():
        h = re.match(r"^(={2,4})\s*(.*?)\s*\1\s*$", line)
        if h:
            club_title, club_name = first_link(h.group(2))
            if not club_title:
                club_name = plain(h.group(2))
                club_title = norm_title(club_name)
            lists_seen = 0
            continue
        if re.search(r"\{\{\s*fs start", line, re.I):
            lists_seen += 1
        for _, end, body in _templates(line):
            if not re.match(r"\s*fs player\s*\|", body, re.I) or not club_title or not lists_seen:
                continue
            _, p = _params(body)
            title, name = first_link(p.get("name", ""))
            name = name or plain(p.get("name", ""))
            note = strip_refs(p.get("other", "")).strip()
            low = plain(note).lower()
            kind = next((k for pat, k in _KIND if re.search(pat, low)), None)
            if not kind or not name:
                continue
            arriving = lists_seen == 1
            if kind == "free" and not arriving:
                kind = "released"
            other_title, other_name = first_link(note) if kind not in ("free", "released") else (None, None)
            if other_title is None and kind in ("permanent", "loan", "end_of_loan"):
                m = re.search(r"\b(?:from|to|at)\s+(.+?)(?:,|$)", plain(note))
                other_name = m.group(1).strip() if m else None
            ref = re.search(r"\|\s*date\s*=\s*([^|}]+)", line[end:])
            row = {
                "page": page,
                "date": parse_date(ref.group(1)) if ref else None,
                "player_name": name,
                "player_title": title,
                "kind": kind,
                "fee_text": None,
                "fee_native": None,
                "currency": None,
                "loan_end": None,
            }
            here = {"title": club_title, "club": club_name}
            there = {"title": other_title, "club": other_name}
            src, dst = (there, here) if arriving else (here, there)
            row |= {
                "from_club": src["club"],
                "from_title": src["title"],
                "to_club": dst["club"],
                "to_title": dst["title"],
            }
            out.append(row)
    return out


def transfer_id(r: dict[str, Any]) -> str:
    key = "|".join(
        str(r.get(k) or "") for k in ("page", "player_title", "player_name", "from_title", "to_title", "kind")
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def parse_page(text: str, page: str, currency: str) -> list[dict[str, Any]]:
    rows = parse_tables(text, page, currency) or parse_by_club(text, page)
    seen: dict[str, dict] = {}
    for r in rows:  # the by-club layout lists a move twice (Out of one club, In of the other)
        seen.setdefault(transfer_id(r), r)
    return [{"transfer_id": k, **v} for k, v in seen.items()]


# -- adapter ------------------------------------------------------------------------------------------------------


def _sparql_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"@en'


def fx_lookup(rates: dict[str, list[tuple[date, float]]], currency: str, on: date | None) -> float | None:
    """Units of `currency` per euro on the last ECB business day up to `on` (the latest when no date)."""
    if currency == "EUR":
        return 1.0
    series = rates.get(currency) or []
    if not series:
        return None
    if on is None:
        return series[-1][1]
    best = None
    for d, v in series:
        if d <= on:
            best = v
        else:
            break
    return best if best is not None else series[0][1]


class WikipediaTransfersAdapter(SourceAdapter):
    name = "wikipedia_transfers"
    scope = "global"
    tier = TIER_DATA
    ttl = 1 * DAY
    rate, per = 1, 2.0  # polite: a handful of pages a night
    respect_robots = True  # /wiki/<title>?action=raw is allowed; /w/ (the API) is not, so it is not used
    base_url = WIKI

    def _api_client(self, name: str, per: float) -> CachedClient:
        # Wikidata SPARQL and the ECB data API are open-data APIs (not crawled pages): robots.txt is not the
        # governing policy there; both ask for a descriptive User-Agent, which we send.
        return CachedClient(
            name,
            cache_dir=self.ctx.settings.cache_dir,
            user_agent=self.ctx.settings.user_agent,
            ttl=7 * DAY if name == "wikidata" else self.ttl,
            rate=1 if self.ctx.throttle else 1e9,
            per=per,
            respect_robots=False,
            provenance=provenance_sink(self.ctx.con),
            transport=self.ctx.transport,
        )

    def pages(self) -> list[tuple[str, str]]:
        cfg = yaml.safe_load(
            (self.ctx.settings.config_dir / "transfer_lists.yaml").read_text(encoding="utf-8")
        )
        return [
            (f"List of {p['country']} football transfers {w}", p.get("currency", "EUR"))
            for w in cfg["windows"]
            for p in cfg["pages"]
        ]

    def ingest(self) -> dict[str, int]:
        rows: list[dict] = []
        pages = 0
        for title, currency in self.pages():
            try:
                res = self.client.get(WIKI + title.replace(" ", "_"), params={"action": "raw"})
            except FetchError as exc:
                if "HTTP 404" not in str(exc):
                    raise
                continue  # not every country has a page for every window
            pages += 1
            for r in parse_page(res.content.decode("utf-8", "replace"), title, currency):
                r["fetched_at"] = res.fetched_at
                rows.append(r)
        if not rows:
            return {"pages": pages}
        rates = self.fx_rates({r["currency"] for r in rows if r.get("currency")} - {"EUR"})
        for r in rows:
            fx = fx_lookup(rates, r["currency"], r["date"]) if r.get("currency") else None
            r["fee_eur"] = round(r["fee_native"] / fx) if r.get("fee_native") is not None and fx else None
        ids = self.wikidata_ids(
            {t for r in rows for t in (r["player_title"], r["from_title"], r["to_title"]) if t}
        )
        for r in rows:
            r["tm_player_id"] = ids.get(r["player_title"], (None, None))[0]
            r["tm_from_club"] = ids.get(r["from_title"], (None, None))[1]
            r["tm_to_club"] = ids.get(r["to_title"], (None, None))[1]
        con = self.ctx.con
        con.execute(
            "DELETE FROM wiki_transfers WHERE page IN (SELECT unnest(?))", [[p for p, _ in self.pages()]]
        )
        return {"pages": pages, "wiki_transfers": upsert(con, "wiki_transfers", rows)}

    def fx_rates(self, currencies: set[str]) -> dict[str, list[tuple[date, float]]]:
        if not currencies:
            return {}
        client = self._api_client("ecb", 1.0)
        res = client.get(
            f"{ECB}D.{'+'.join(sorted(currencies))}.EUR.SP00.A",
            params={"startPeriod": f"{self.ctx.now.year - 1}-01-01", "format": "csvdata"},
        )
        out: dict[str, list[tuple[date, float]]] = {}
        fx_rows = []
        lines = res.content.decode().splitlines()
        if not lines:
            return out
        head = lines[0].split(",")
        ic, it, iv = head.index("CURRENCY"), head.index("TIME_PERIOD"), head.index("OBS_VALUE")
        for ln in lines[1:]:
            f = ln.split(",")
            if len(f) <= iv or not f[iv]:
                continue
            d, v = date.fromisoformat(f[it]), float(f[iv])
            out.setdefault(f[ic], []).append((d, v))
            fx_rows.append({"date": d, "currency": f[ic], "per_eur": v, "source": "ecb"})
        for s in out.values():
            s.sort()
        upsert(self.ctx.con, "fx_rates", fx_rows)
        return out

    def wikidata_ids(self, titles: set[str], batch: int = 80) -> dict[str, tuple[str | None, str | None]]:
        """Article title -> (Transfermarkt player id, Transfermarkt club id) via Wikidata sitelinks."""
        client = self._api_client("wikidata", 5.0)
        out: dict[str, tuple[str | None, str | None]] = {}
        todo = sorted(titles)
        for k in range(0, len(todo), batch):
            values = " ".join(_sparql_str(t) for t in todo[k : k + batch])
            q = f"""SELECT ?t ?p ?c WHERE {{
  VALUES ?t {{ {values} }}
  ?a schema:name ?t ; schema:isPartOf <https://en.wikipedia.org/> ; schema:about ?i .
  OPTIONAL {{ ?i wdt:P2446 ?p }} OPTIONAL {{ ?i wdt:P7223 ?c }}
}}"""
            try:
                res = client.request(
                    "POST",
                    WIKIDATA,
                    data=f"query={quote_plus(q)}".encode(),
                    headers={
                        "Accept": "application/sparql-results+json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
            except FetchError as exc:  # the query service is often overloaded; rows keep their names
                log.warning("wikidata ids skipped for %d titles: %s", len(todo[k : k + batch]), exc)
                continue
            for b in res.json()["results"]["bindings"]:
                t = b["t"]["value"]
                p, c = out.get(t, (None, None))
                out[t] = (p or b.get("p", {}).get("value"), c or b.get("c", {}).get("value"))
        return out
