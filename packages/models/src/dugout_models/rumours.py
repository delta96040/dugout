"""Transfer rumour track record: what became of 25 years of the Guardian's Rumour Mill.

Each archived paragraph (`rumours`, adapter rumour_archive) names players in bold and mentions clubs. A *link* is
(rumour, player, club) for every club mentioned near the player other than his own club (and the club that loaned
him out). Players and clubs are resolved to Transfermarkt ids by normalised name; the outcome is whether he joined
that club within 3, 6 or 12 months (transfermarkt-datasets transfers to 2026-07-06, then Wikipedia's summer-2026
transfer lists via `wiki_transfers`). Links whose window is not over yet stay unlabelled.

Two estimates, both trained on older seasons and tested on the latest (docs/backtests/rumours.md):
- per outlet: the share of its links that came true, shrunk towards the overall rate (beta-binomial empirical Bayes)
  so an outlet with a handful of rumours is not called perfect or hopeless;
- per rumour: a logistic regression on the outlet's shrunk rate, the buyer/seller wealth gap, the player's age and
  value, how close the transfer deadline is, how often the same link was reported before, and how many clubs the
  item names. Contract length is *not* used: only today's contracts exist, and for old rumours they leak the outcome
  (a player who moved has a new contract).
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from dugout_pipeline.clubs import normalise
from dugout_pipeline.db import upsert
from scipy.optimize import minimize
from scipy.special import betaln, expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from dugout_models.valuation import TM_DIR

log = logging.getLogger(__name__)

HORIZONS = (3, 6, 12)
SHIPPED_HORIZON = 6  # months: the window a rumour is about (this window or the next), shown on the site
TM_SNAPSHOT = pd.Timestamp("2026-07-06")  # transfermarkt-datasets is frozen here
SUMMER_2026_CLOSE = pd.Timestamp("2026-09-02")  # Wikipedia's summer-2026 lists cover moves up to the deadline
FIRST_SEASON = 2004  # 2004-05: Transfermarkt's transfer histories thin out before (docs/backtests/rumours.md)
MONTH = pd.Timedelta(days=30.44)
COLUMN = "The Guardian"  # outlet of an item the column does not credit to anyone else
FEATURES = [
    "outlet_logit",
    "credited",
    "age",
    "age2",
    "log_mv",
    "buyer_value",
    "seller_value",
    "wealth_gap",
    "buyer_unknown",
    "same_league",
    "log_days_to_deadline",
    "in_window",
    "log_repeats",
    "n_clubs",
    "n_players",
]

# Short names the column uses that are not Transfermarkt names, -> a Transfermarkt club code (clubs.parquet).
ALIASES = {
    "spurs": "tottenham-hotspur",
    "man utd": "manchester-united",
    "man united": "manchester-united",
    "man city": "manchester-city",
    "psg": "fc-paris-saint-germain",
    "paris": "fc-paris-saint-germain",
    "paris st germain": "fc-paris-saint-germain",
    "barca": "fc-barcelona",
    "barcelona": "fc-barcelona",
    "atletico": "atletico-madrid",
    "atletico madrid": "atletico-madrid",
    "atleti": "atletico-madrid",
    "bayern": "fc-bayern-munchen",
    "juve": "juventus-turin",
    "juventus": "juventus-turin",
    "dortmund": "borussia-dortmund",
    "gladbach": "borussia-monchengladbach",
    "leverkusen": "bayer-04-leverkusen",
    "inter": "inter-mailand",
    "internazionale": "inter-mailand",
    "milan": "ac-mailand",
    "wolves": "wolverhampton-wanderers",
    "west brom": "west-bromwich-albion",
    "qpr": "queens-park-rangers",
    "psv": "psv-eindhoven",
    "ajax": "ajax-amsterdam",
    "benfica": "benfica-lissabon",
    "porto": "fc-porto",
    "sporting": "sporting-lissabon",
    "sporting lisbon": "sporting-lissabon",
    "celtic": "celtic-glasgow",
    "rangers": "glasgow-rangers",
    "lyon": "olympique-lyon",
    "marseille": "olympique-marseille",
    "monaco": "as-monaco",
    "roma": "as-rom",
    "napoli": "ssc-neapel",
    "fiorentina": "ac-florenz",
    "lazio": "lazio-rom",
    "zenit": "zenit-st-petersburg",
    "galatasaray": "galatasaray-istanbul",
    "fenerbahce": "fenerbahce-istanbul",
    "besiktas": "besiktas-istanbul",
    "sevilla": "fc-sevilla",
    "seville": "fc-sevilla",
    "valencia": "fc-valencia",
    "villarreal": "fc-villarreal",
    "real sociedad": "real-sociedad-san-sebastian",
    "athletic bilbao": "athletic-bilbao",
    "bilbao": "athletic-bilbao",
}
# names that are words, places or parts of other names, never a club on their own
GENERIC = {
    "united", "city", "town", "county", "rovers", "real", "olympic", "olympique", "athletic", "wanderers", "albion",
    "national", "nacional", "union", "sport", "sporting club", "club", "academy", "independiente", "racing",
    "dynamo", "dinamo", "fc", "sc", "ac", "as", "cd", "free agent", "without club", "retired", "unknown",
    "career break", "ban", "england", "brazil", "spain", "france", "germany", "italy", "portugal", "argentina",
}  # fmt: skip
YOUTH = re.compile(r"\b(u ?\d{2}|ii|iii|b|c|reserves?|youth|academy|yth|jgd|juniors?|primavera)\b")
MIN_MOVES = 15  # a club must appear in this many Transfermarkt moves to be in the gazetteer


def fold(name: str) -> str:
    """Lower-case, accents removed: the key agents use for a player named in a rumour (tools._fold_name)."""
    return "".join(c for c in unicodedata.normalize("NFKD", name.lower()) if not unicodedata.combining(c))


def person_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]+", " ", s.replace("-", " ").replace("'", "").replace("’", ""))
    return re.sub(r"\s+", " ", s).strip()


# ---- resolving names ---------------------------------------------------------------------------------------


@dataclass
class Gazetteer:
    """Normalised club name -> Transfermarkt club id; the most-used club wins a shared name."""

    names: dict[str, str] = field(default_factory=dict)
    label: dict[str, str] = field(default_factory=dict)
    longest: int = 5

    @classmethod
    def build(cls, transfers: pd.DataFrame, clubs: pd.DataFrame) -> Gazetteer:
        moves = pd.concat(
            [
                transfers[["to_club_id", "to_club_name"]].set_axis(["id", "name"], axis=1),
                transfers[["from_club_id", "from_club_name"]].set_axis(["id", "name"], axis=1),
            ]
        ).dropna()
        moves["id"] = moves["id"].astype(int).astype(str)
        popularity = moves.groupby("id").size()
        cand: dict[str, dict[str, int]] = defaultdict(dict)
        label: dict[str, str] = {}

        def add(name: str, cid: str) -> None:
            n = normalise(name)
            if (
                len(n) < 3
                or n in GENERIC
                or YOUTH.search(n)
                or any(ch.isdigit() for ch in n.replace("04", ""))
            ):
                return
            cand[n][cid] = int(popularity.get(cid, 0))

        for (cid, name), n in moves.groupby(["id", "name"]).size().items():
            if popularity.get(cid, 0) >= MIN_MOVES and n >= 3:
                add(name, cid)
                label.setdefault(cid, name)
        codes = {}
        for r in clubs.itertuples():
            cid = str(r.club_id)
            add(r.name, cid)
            add(r.club_code.replace("-", " "), cid)
            codes[r.club_code] = cid
            label.setdefault(cid, r.name)
        names = {n: max(ids, key=ids.get) for n, ids in cand.items()}
        for alias, code in ALIASES.items():
            if code in codes:
                names[normalise(alias)] = codes[code]
        return cls(names=names, label=label, longest=max(len(n.split()) for n in names))

    def find(self, text: str) -> list[tuple[str, str]]:
        """(club id, matched name) for every club named in the text, longest names first, no overlaps."""
        toks = normalise(text).split()
        out, i = [], 0
        while i < len(toks):
            for k in range(min(self.longest, len(toks) - i), 0, -1):
                name = " ".join(toks[i : i + k])
                if name in self.names:
                    out.append((self.names[name], name))
                    i += k
                    break
            else:
                i += 1
        return out

    def resolve(self, name: str) -> str | None:
        hits = self.find(name)
        return hits[0][0] if hits else None


@dataclass
class People:
    """Transfermarkt players by normalised name, plus each player's club history."""

    by_name: dict[str, list[int]]
    dob: dict[int, pd.Timestamp]
    peak: dict[int, float]
    last_season: dict[int, float]
    moves: dict[int, list[tuple[pd.Timestamp, str, str, float]]]  # (date, from, to, fee), by date

    @classmethod
    def build(cls, players: pd.DataFrame, transfers: pd.DataFrame) -> People:
        by_name: dict[str, list[int]] = defaultdict(list)
        for pid, name in zip(players.player_id, players.name, strict=True):
            if isinstance(name, str):
                by_name[person_key(name)].append(int(pid))
        tr = transfers[pd.to_datetime(transfers.transfer_date) <= TM_SNAPSHOT].copy()
        tr["d"] = pd.to_datetime(tr.transfer_date)
        tr = tr.sort_values("d")
        moves: dict[int, list] = defaultdict(list)
        for r in tr.itertuples():
            moves[int(r.player_id)].append(
                (
                    r.d,
                    _cid(r.from_club_id),
                    _cid(r.to_club_id),
                    float(pd.to_numeric(r.transfer_fee, errors="coerce") or 0),
                )
            )
        idx = players.set_index("player_id")
        return cls(
            by_name=dict(by_name),
            dob=pd.to_datetime(idx.date_of_birth).to_dict(),
            peak=idx.highest_market_value_in_eur.fillna(0).to_dict(),
            last_season=idx.last_season.to_dict(),
            moves=dict(moves),
        )

    def candidates(self, name: str, d: pd.Timestamp) -> list[int]:
        key = person_key(name)
        ids = self.by_name.get(key) or self.by_name.get(" ".join(reversed(key.split())), [])
        out = []
        for pid in ids:
            dob = self.dob.get(pid)
            if dob is None or pd.isna(dob) or not (15 <= (d - dob).days / 365.25 <= 42):
                continue
            last = self.last_season.get(pid)
            if last is not None and not pd.isna(last) and last < d.year - 1:
                continue
            out.append(pid)
        return out

    def club_on(self, pid: int, d: pd.Timestamp) -> tuple[str | None, str | None]:
        """(club, club that loaned him out or None) on day d, from his transfer history."""
        before = [m for m in self.moves.get(pid, []) if m[0] <= d]
        if not before:
            return None, None
        when, frm, to, fee = before[-1]
        # a fee-less move in the last 13 months may be a loan: the club he came from may still own him
        parent = frm if fee == 0 and (d - when).days < 400 else None
        return to, parent


def _cid(x: Any) -> str | None:
    return None if x is None or pd.isna(x) else str(int(x))


SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“‘'])")


def links_from(rumours: pd.DataFrame, gaz: Gazetteer, people: People) -> tuple[pd.DataFrame, dict[str, int]]:
    """Resolve archived paragraphs to (rumour, player, linked club) rows. Returns the rows and match counts."""
    stats = defaultdict(int)
    rows = []
    for r in rumours.itertuples():
        d = (
            pd.Timestamp(r.published_at).tz_convert(None)
            if pd.Timestamp(r.published_at).tzinfo
            else pd.Timestamp(r.published_at)
        )
        names = [n for n in r.players if n]
        sentences = SENTENCE.split(r.body_text)
        # take the players' own names out of the text so a surname is never read as a club
        strip = sorted(
            {n for n in names} | {n.split()[-1] for n in names if len(n.split()[-1]) >= 4},
            key=len,
            reverse=True,
        )
        pattern = re.compile("|".join(re.escape(s) for s in strip), flags=re.I) if strip else None
        all_clubs = {c for c, _ in gaz.find(pattern.sub(" ", r.body_text) if pattern else r.body_text)}
        resolved = []
        for name in names:
            stats["names"] += 1
            cands = people.candidates(name, d)
            if not cands:
                stats["names_unmatched"] += 1
                continue
            if len(cands) > 1:
                stats["names_ambiguous"] += 1
                # prefer the namesake whose club the item mentions, then the more valuable
                cands.sort(
                    key=lambda p: (people.club_on(p, d)[0] in all_clubs, people.peak.get(p, 0)), reverse=True
                )
            resolved.append((name, cands[0]))
        stats["names_matched"] += len(resolved)
        for name, pid in resolved:
            surname = name.split()[-1].lower()
            near = [s for s in sentences if name.lower() in s.lower() or surname in s.lower()] or [
                r.body_text
            ]
            text = " ".join(near)
            clubs = gaz.find(pattern.sub(" ", text) if pattern else text)
            own, parent = people.club_on(pid, d)
            linked: dict[str, str] = {}
            for cid, matched in clubs:
                if cid not in (own, parent):
                    linked.setdefault(cid, matched)
            if not linked:
                stats["players_without_club"] += 1
            for cid, matched in linked.items():
                rows.append(
                    {
                        "link_id": f"{r.rumour_id}:{pid}:{cid}",
                        "rumour_id": r.rumour_id,
                        "published_at": d,
                        "outlet": r.outlet,
                        "player_name": name,
                        "tm_player_id": str(pid),
                        "club_name": gaz.label.get(cid, matched),
                        "tm_club_id": cid,
                        "from_tm_club": own,
                        "n_clubs": len(linked),
                        "n_players": len(names),
                    }
                )
    stats["links"] = len(rows)
    return pd.DataFrame(rows), dict(stats)


# ---- outcomes ----------------------------------------------------------------------------------------------


def all_moves(transfers: pd.DataFrame, wiki: pd.DataFrame | None, gaz: Gazetteer) -> pd.DataFrame:
    """Every recorded arrival (player, club, date): Transfermarkt to its snapshot, then Wikipedia's lists."""
    tm = transfers[pd.to_datetime(transfers.transfer_date) <= TM_SNAPSHOT]
    out = pd.DataFrame(
        {
            "tm_player_id": tm.player_id.astype(int).astype(str),
            "tm_club_id": tm.to_club_id.map(_cid),
            "date": pd.to_datetime(tm.transfer_date),
        }
    )
    if wiki is not None and len(wiki):
        w = wiki[wiki.tm_player_id.notna() & (wiki.kind != "end_of_loan")].copy()
        club = w.tm_to_club.where(
            w.tm_to_club.notna(), w.to_club.map(lambda n: gaz.resolve(n) if isinstance(n, str) else None)
        )
        # a by-club page without a date: the summer window it lists
        when = pd.to_datetime(w.date).fillna(pd.Timestamp("2026-07-01"))
        out = pd.concat(
            [
                out,
                pd.DataFrame({"tm_player_id": w.tm_player_id.astype(str), "tm_club_id": club, "date": when}),
            ]
        )
    return out.dropna().drop_duplicates()


def label(links: pd.DataFrame, moves: pd.DataFrame, coverage_end: pd.Timestamp) -> pd.DataFrame:
    links = links.copy()
    m = links[["link_id", "tm_player_id", "tm_club_id", "published_at"]].merge(
        moves, on=["tm_player_id", "tm_club_id"], how="left"
    )
    m = m[m.date > m.published_at]
    first = m.groupby("link_id").date.min()
    links["joined_on"] = links.link_id.map(first)
    for n in HORIZONS:
        end = links.published_at + n * MONTH
        col = (links.joined_on.notna() & (links.joined_on <= end)).astype(object)
        col[end > coverage_end] = None  # the window is not over yet
        links[f"joined_{n}m"] = col
    return links


def coverage(wiki: pd.DataFrame | None) -> pd.Timestamp:
    return SUMMER_2026_CLOSE if wiki is not None and len(wiki) else TM_SNAPSHOT


# ---- features ----------------------------------------------------------------------------------------------


def club_values(valuations: pd.DataFrame) -> pd.DataFrame:
    """Squad value (sum of the latest valuation in the year before) per club on 1 January and 1 July each year."""
    v = valuations[["player_id", "date", "market_value_in_eur", "current_club_id"]].copy()
    v["date"] = pd.to_datetime(v.date)
    rows = []
    for year in range(2003, 2027):
        for month in (1, 7):
            s = pd.Timestamp(year=year, month=month, day=1)
            w = (
                v[(v.date < s) & (v.date >= s - pd.Timedelta(days=365))]
                .sort_values("date")
                .groupby("player_id")
                .tail(1)
            )
            tot = w.groupby("current_club_id").market_value_in_eur.sum()
            rows.append(
                pd.DataFrame({"snap": s, "club": tot.index.astype(int).astype(str), "value": tot.values})
            )
    return pd.concat(rows)


def next_deadline(d: pd.Timestamp) -> pd.Timestamp:
    for cand in (pd.Timestamp(d.year, 2, 1), pd.Timestamp(d.year, 9, 1), pd.Timestamp(d.year + 1, 2, 1)):
        if cand >= d.normalize():
            return cand
    return pd.Timestamp(d.year + 1, 2, 1)


def features(links: pd.DataFrame, t: dict[str, pd.DataFrame], values: pd.DataFrame) -> pd.DataFrame:
    x = links.copy().sort_values("published_at").reset_index(drop=True)
    d = x.published_at
    dob = pd.to_datetime(t["players"].set_index("player_id").date_of_birth)
    x["age"] = (d - x.tm_player_id.astype(int).map(dob)).dt.days / 365.25
    x["age2"] = (x.age - 25) ** 2
    vals = t["player_valuations"][["player_id", "date", "market_value_in_eur"]].copy()
    vals["date"] = pd.to_datetime(vals.date)
    vals["tm_player_id"] = vals.player_id.astype(str)
    asof = pd.merge_asof(
        x[["published_at", "tm_player_id"]].reset_index().sort_values("published_at"),
        vals.sort_values("date").rename(columns={"date": "published_at"})[
            ["published_at", "tm_player_id", "market_value_in_eur"]
        ],
        on="published_at",
        by="tm_player_id",
        direction="backward",
    ).set_index("index")["market_value_in_eur"]
    x["log_mv"] = np.log1p(asof.reindex(x.index))
    snap = d.map(lambda s: pd.Timestamp(s.year, 7 if s.month >= 7 else 1, 1))
    val = values.set_index(["snap", "club"]).value
    buyer = pd.Series(
        [val.get((s, c), np.nan) for s, c in zip(snap, x.tm_club_id, strict=True)], index=x.index
    )
    seller = pd.Series(
        [val.get((s, c), np.nan) for s, c in zip(snap, x.from_tm_club, strict=True)], index=x.index
    )
    x["buyer_unknown"] = buyer.isna().astype(float)
    x["buyer_value"] = np.log1p(buyer.fillna(0))
    x["seller_value"] = np.log1p(seller.fillna(0))
    x["wealth_gap"] = x.buyer_value - x.seller_value
    league = t["clubs"].assign(club=lambda c: c.club_id.astype(str)).set_index("club").domestic_competition_id
    x["same_league"] = (
        x.tm_club_id.map(league).fillna("?b") == x.from_tm_club.map(league).fillna("?s")
    ).astype(float)
    days = (d.map(next_deadline) - d.dt.normalize()).dt.days
    x["log_days_to_deadline"] = np.log1p(days)
    x["in_window"] = ((d.dt.month == 1) | (d.dt.month.isin([6, 7, 8]))).astype(float)
    # how often this player was linked with this club in the year before (any outlet)
    rep = []
    seen: dict[tuple[str, str], list[pd.Timestamp]] = defaultdict(list)
    for p, c, when in zip(x.tm_player_id, x.tm_club_id, d, strict=True):
        past = seen[(p, c)]
        rep.append(sum(1 for w in past if (when - w).days <= 365 and w.normalize() < when.normalize()))
        past.append(when)
    x["log_repeats"] = np.log1p(rep)
    x["credited"] = (x.outlet != COLUMN).astype(float)
    x["season"] = d.dt.year - (d.dt.month < 7)
    return x


# ---- estimates ---------------------------------------------------------------------------------------------


def beta_prior(hits: np.ndarray, n: np.ndarray) -> tuple[float, float]:
    """Beta(a, b) maximising the beta-binomial marginal likelihood of the outlets' records."""

    def nll(z):
        a, b = np.exp(z)
        return -np.sum(betaln(hits + a, n - hits + b) - betaln(a, b))

    p = max(hits.sum() / max(n.sum(), 1), 1e-3)
    res = minimize(nll, np.log([p * 20, (1 - p) * 20]), method="Nelder-Mead")
    a, b = np.exp(res.x)
    return float(a), float(b)


def outlet_rates(train: pd.DataFrame, y: str) -> tuple[dict[str, dict[str, float]], tuple[float, float]]:
    g = train.groupby("outlet")[y].agg(["sum", "count"])
    a, b = beta_prior(g["sum"].to_numpy(float), g["count"].to_numpy(float))
    rates = {
        o: {
            "n": int(r["count"]),
            "hits": int(r["sum"]),
            "raw": r["sum"] / r["count"],
            "shrunk": (r["sum"] + a) / (r["count"] + a + b),
        }
        for o, r in g.iterrows()
    }
    return rates, (a, b)


def _outlet_logit(x: pd.DataFrame, rates: dict, prior: tuple[float, float]) -> np.ndarray:
    base = prior[0] / sum(prior)
    p = x.outlet.map(lambda o: rates.get(o, {}).get("shrunk", base)).to_numpy(float)
    return logit(np.clip(p, 1e-4, 1 - 1e-4))


@dataclass
class RumourModel:
    rates: dict
    prior: tuple[float, float]
    scaler: StandardScaler
    clf: LogisticRegression
    medians: dict[str, float]

    @classmethod
    def fit(cls, train: pd.DataFrame, y: str) -> RumourModel:
        rates, prior = outlet_rates(train, y)
        # the outlet feature on training rows comes from the other rows' records (leave-one-out), not their own
        x = train.copy()
        g = train.groupby("outlet")[y].agg(["sum", "count"])
        s, c = x.outlet.map(g["sum"]), x.outlet.map(g["count"])
        loo = (s - x[y].astype(float) + prior[0]) / (c - 1 + sum(prior))
        x["outlet_logit"] = logit(np.clip(loo, 1e-4, 1 - 1e-4))
        medians = {f: float(x[f].median()) for f in FEATURES}
        X = x[FEATURES].fillna(medians).to_numpy(float)
        scaler = StandardScaler().fit(X)
        clf = LogisticRegression(C=1.0, max_iter=2000).fit(scaler.transform(X), x[y].astype(int))
        return cls(rates, prior, scaler, clf, medians)

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        x = x.copy()
        x["outlet_logit"] = _outlet_logit(x, self.rates, self.prior)
        X = x[FEATURES].fillna(self.medians).to_numpy(float)
        return self.clf.predict_proba(self.scaler.transform(X))[:, 1]

    def outlet_p(self, x: pd.DataFrame) -> np.ndarray:
        return expit(_outlet_logit(x, self.rates, self.prior))


def scores(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    out = {
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "mean_p": float(p.mean()),
    }
    if 0 < y.sum() < len(y):
        from sklearn.metrics import roc_auc_score

        out["auc"] = float(roc_auc_score(y, p))
    return out


def calibration(p: np.ndarray, y: np.ndarray, edges=(0, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0)) -> list[dict]:
    out = []
    for lo, hi in itertools.pairwise(edges):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo)
        if m.sum():
            out.append(
                {
                    "bin": f"{lo:.0%}-{hi:.0%}",
                    "n": int(m.sum()),
                    "mean_p": float(p[m].mean()),
                    "rate": float(y[m].mean()),
                }
            )
    return out


# ---- assembling -------------------------------------------------------------------------------------------


def load_tm(tm_dir: Path = TM_DIR) -> dict[str, pd.DataFrame]:
    return {
        n: pd.read_parquet(tm_dir / f"{n}.parquet")
        for n in ("players", "transfers", "player_valuations", "clubs")
    }


def build(
    con: duckdb.DuckDBPyConnection, t: dict[str, pd.DataFrame] | None = None
) -> tuple[pd.DataFrame, dict]:
    """All links with labels and features, plus match statistics."""
    t = t or load_tm()
    rumours = con.execute("SELECT rumour_id, published_at, players, outlet, body_text FROM rumours").df()
    wiki = con.execute("SELECT * FROM wiki_transfers").df() if _has(con, "wiki_transfers") else None
    gaz = Gazetteer.build(t["transfers"], t["clubs"])
    people = People.build(t["players"], t["transfers"])
    links, stats = links_from(rumours, gaz, people)
    stats["paragraphs"] = len(rumours)
    stats["paragraphs_linked"] = int(links.rumour_id.nunique()) if len(links) else 0
    cov = coverage(wiki)
    links = label(links, all_moves(t["transfers"], wiki, gaz), cov)
    links = features(links, t, club_values(t["player_valuations"]))
    stats["coverage_end"] = str(cov.date())
    return links, stats


def _has(con, table: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
    )


def backtest(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path | None = None,
    links: pd.DataFrame | None = None,
    stats: dict | None = None,
) -> dict:
    if links is None:
        links, stats = build(con)
    stats = stats or {}
    report: dict[str, Any] = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "matching": stats,
        "horizons": {},
    }
    links = links[links.season >= FIRST_SEASON]
    for n in HORIZONS:
        y = f"joined_{n}m"
        lab = links[links[y].notna()].copy()
        lab[y] = lab[y].astype(bool)
        test_season = int(lab.season.max())
        train, test = lab[lab.season < test_season], lab[lab.season == test_season]
        model = RumourModel.fit(train, y)
        yt = test[y].to_numpy(float)
        base = np.full(len(test), train[y].mean())
        p_model, p_outlet = model.predict(test), model.outlet_p(test)
        by_season = lab.groupby("season")[y].agg(["mean", "count"])
        report["horizons"][str(n)] = {
            "train_seasons": f"{int(train.season.min())}-{int(train.season.max()) + 1 - 2000:02d}",
            "test_season": f"{test_season}-{test_season + 1 - 2000:02d}",
            "n_train": len(train),
            "n_test": len(test),
            "train_rate": float(train[y].mean()),
            "test_rate": float(yt.mean()),
            "base_rate": scores(base, yt),
            "outlet_only": scores(p_outlet, yt),
            "model": scores(p_model, yt),
            "calibration": calibration(p_model, yt),
            "coefficients": dict(zip(FEATURES, np.round(model.clf.coef_[0], 3).tolist(), strict=True)),
            "prior": {"a": model.prior[0], "b": model.prior[1]},
            "outlets": {
                o: r for o, r in sorted(model.rates.items(), key=lambda kv: -kv[1]["n"]) if r["n"] >= 20
            },
            "by_season": {
                f"{s}-{s + 1 - 2000:02d}": {"rate": float(r["mean"]), "n": int(r["count"])}
                for s, r in by_season.iterrows()
            },
        }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "rumours.json").write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
        (out_dir / "rumours.md").write_text(report_md(report), encoding="utf-8")
    return report


def report_md(r: dict) -> str:
    s = r["matching"]
    pct = lambda a, b: f"{a / b:.0%}" if b else "-"  # noqa: E731
    lines = [
        "# Transfer rumour track record backtest",
        "",
        f"Generated {r['generated']}. The Guardian's Rumour Mill archive (Guardian Open Platform) x what happened",
        f"(transfermarkt-datasets transfers to 2026-07-06, Wikipedia summer-2026 lists to {s.get('coverage_end')}).",
        "",
        "## Matching",
        "",
        f"- Paragraphs naming a player: {s.get('paragraphs', 0)}; player names: {s.get('names', 0)}.",
        f"- Names matched to one Transfermarkt player: {s.get('names_matched', 0)} ({pct(s.get('names_matched', 0), s.get('names', 0))});"
        f" {s.get('names_ambiguous', 0)} had namesakes (resolved by the club the item mentions, then by value);"
        f" {s.get('names_unmatched', 0)} unmatched (bold club names, nicknames, surnames only, players outside the dataset).",
        f"- Matched players with no other club named near them: {s.get('players_without_club', 0)}.",
        f"- Links (rumour x player x club): {s.get('links', 0)}, from {s.get('paragraphs_linked', 0)} paragraphs"
        f" ({pct(s.get('paragraphs_linked', 0), s.get('paragraphs', 0))}).",
        "",
        "## Results (train on older seasons, test on the latest fully observed one)",
        "",
        "| Horizon | Train | Test | Test links | Came true | Base rate Brier | Outlet only | Model | Base log loss | Model log loss | Model AUC |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n, h in r["horizons"].items():
        lines.append(
            f"| {n} months | {h['train_seasons']} ({h['n_train']}) | {h['test_season']} | {h['n_test']} | {h['test_rate']:.1%} |"
            f" {h['base_rate']['brier']:.4f} | {h['outlet_only']['brier']:.4f} | **{h['model']['brier']:.4f}** |"
            f" {h['base_rate']['log_loss']:.4f} | {h['model']['log_loss']:.4f} | {h['model'].get('auc', float('nan')):.3f} |"
        )
    h = r["horizons"][str(SHIPPED_HORIZON)]
    lines += [
        "",
        f"## Shipped horizon: {SHIPPED_HORIZON} months",
        "",
        "Calibration on the test season:",
        "",
        "| Predicted | Links | Mean predicted | Came true |",
        "|---|---|---|---|",
        *[f"| {c['bin']} | {c['n']} | {c['mean_p']:.1%} | {c['rate']:.1%} |" for c in h["calibration"]],
        "",
        "Coefficients (standardised): " + ", ".join(f"{k} {v}" for k, v in h["coefficients"].items()),
        "",
        f"Outlet prior Beta({h['prior']['a']:.2f}, {h['prior']['b']:.2f}). Outlets with 20+ training links:",
        "",
        "| Outlet | Links | Came true | Raw rate | Shrunk rate |",
        "|---|---|---|---|---|",
        *[
            f"| {o} | {v['n']} | {v['hits']} | {v['raw']:.1%} | {v['shrunk']:.1%} |"
            for o, v in h["outlets"].items()
        ],
        "",
        "Rate by season (all labelled links):",
        "",
        "| Season | Links | Came true |",
        "|---|---|---|",
        *[f"| {s} | {v['n']} | {v['rate']:.1%} |" for s, v in h["by_season"].items()],
        "",
    ]
    return "\n".join(lines) + "\n"


# ---- publishing --------------------------------------------------------------------------------------------


def write_links(con: duckdb.DuckDBPyConnection, links: pd.DataFrame, now: datetime) -> int:
    cols = ["link_id", "rumour_id", "published_at", "outlet", "player_name", "tm_player_id", "club_name", "tm_club_id",
            "from_tm_club", "joined_3m", "joined_6m", "joined_12m", "joined_on"]  # fmt: skip
    df = links[cols].copy()
    df["published_at"] = pd.to_datetime(df.published_at).dt.tz_localize("UTC")
    df["joined_on"] = pd.to_datetime(df.joined_on).dt.date
    df["labelled_at"] = now
    con.execute("DELETE FROM rumour_links")
    return upsert(con, "rumour_links", df.to_dict("records"))


def publish(con: duckdb.DuckDBPyConnection, now: datetime, recent_days: int = 120) -> list[dict]:
    """Outlet track records (all labelled links) and the chance each recent rumour comes true, for the site.

    Rows: entity_type 'outlet' (metrics links / came_true / hit_rate, shrunk) and entity_type 'rumour' with
    entity_id = the paragraph's news_id and context '<club slug>:<folded player name>' for every dugout club
    the item links with the player (metrics p_true, one_in). Also rewrites the rumour_links table."""
    from dugout_models.publish import _row

    links, _ = build(con)
    write_links(con, links, now)
    links = links[links.season >= FIRST_SEASON]
    y = f"joined_{SHIPPED_HORIZON}m"
    lab = links[links[y].notna()].copy()
    lab[y] = lab[y].astype(bool)
    model = RumourModel.fit(lab, y)
    rows = [_row("rumours", "overall", "all", "hit_rate", float(lab[y].mean()), now, f"{SHIPPED_HORIZON}m"),
            _row("rumours", "overall", "all", "links", len(lab), now, f"{SHIPPED_HORIZON}m")]  # fmt: skip
    for outlet, r in model.rates.items():
        for metric, v in (("links", r["n"]), ("came_true", r["hits"]), ("hit_rate", r["shrunk"])):
            rows.append(_row("rumours", "outlet", outlet, metric, v, now, f"{SHIPPED_HORIZON}m"))
    slugs = dict(
        con.execute(
            "SELECT external_id, dugout_id FROM entity_ids WHERE entity_type = 'club' AND provider = 'transfermarkt'"
        ).fetchall()
    )
    cutoff = pd.Timestamp(now).tz_convert(None) - pd.Timedelta(days=recent_days)
    recent = links[(links.published_at >= cutoff) & links.tm_club_id.isin(slugs)].copy()
    if len(recent):
        recent["p"] = model.predict(recent)
        for r in recent.itertuples():
            ctx = f"{slugs[r.tm_club_id]}:{fold(r.player_name)}"
            rows.append(_row("rumours", "rumour", r.rumour_id, "p_true", float(r.p), now, ctx))
            rows.append(
                _row(
                    "rumours",
                    "rumour",
                    r.rumour_id,
                    "one_in",
                    int(max(1, round(1 / max(r.p, 1e-3)))),
                    now,
                    ctx,
                )
            )
    return rows
