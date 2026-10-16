"""Transfer rumour track record: what became of 25 years of the Guardian's Rumour Mill.

Each archived paragraph (`rumours`, adapter rumour_archive) names players in bold and mentions clubs. A *link* is
(rumour, player, club) for every club mentioned near the player other than his own club (and the club that loaned
him out). Players and clubs are resolved to Transfermarkt ids by normalised name; the outcome is whether he joined
that club within 3, 6 or 12 months (transfermarkt-datasets club timelines to 2026-07-06, see People; then Wikipedia's summer-2026
transfer lists via `wiki_transfers`). Links whose window is not over yet stay unlabelled.

Two estimates, both trained on older seasons and tested on the latest (docs/backtests/rumours.md):
- per outlet: the share of its links that came true, shrunk towards the overall rate (beta-binomial empirical Bayes)
  so an outlet with a handful of rumours is not called perfect or hopeless;
- per rumour: a logistic regression on the buyer/seller wealth gap, the player's age and value, the transfer
  window and how close its deadline is, how often the same link was reported before, how many clubs and players the
  item names, and the column's era. The outlet's record made validation worse, so it is published but not used.
  Contract length is *not* used: only today's contracts exist, and for old rumours they leak the outcome (a player
  who moved has a new contract).
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
# Shipped features. The outlet's shrunk record (OUTLET_FEATURES) made validation seasons 2023-24 and 2024-25 worse,
# so it is published as a track record but not used to score a rumour (rumours.md).
OUTLET_FEATURES = ["outlet_logit", "credited"]
FEATURES = [
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
    "modern_column",
]
# From 2020 the column runs far fewer, shorter items (about 250 columns a year before, 40-90 after) and many more
# of them come true; the era is known when a rumour is published, so it is a feature (rumours.md, by season).
MODERN_SEASON = 2020

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
    "van", "den", "der", "de", "la", "el", "al", "st", "san", "santa", "start", "top", "star", "sun",
}  # fmt: skip
YOUTH = re.compile(r"\b(u ?\d{2}|ii|iii|b|c|reserves?|youth|academy|yth|jgd|juniors?|primavera)\b")
MIN_MOVES = 15  # a club must appear in this many Transfermarkt moves to be in the gazetteer
# a run of capitalised words, allowing the small words inside club names ("Atlético de Madrid", "Brighton & Hove")
_CAP = r"(?:[A-ZÀ-ÖØ-Þ0-9][\w'’.\-]*)"
_JOIN = r"(?:\s+(?:de|del|della|di|da|do|dos|la|le|of|and|upon|am|an|der|&)(?=\s+[A-ZÀ-ÖØ-Þ]))?"
CAPITALISED = re.compile(rf"{_CAP}(?:{_JOIN}\s+{_CAP})*")


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
        """(club id, matched name) for every club named in the text, longest names first, no overlaps.
        Only runs of capitalised words count ("Reading", "Start", "Nice" as clubs, not as words)."""
        out = []
        for run in CAPITALISED.findall(text):
            out += self._match(normalise(run).split())
        return out

    def _match(self, toks: list[str]) -> list[tuple[str, str]]:
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
    """Transfermarkt players by normalised name, and where each one played when.

    transfermarkt-datasets only carries transfer histories for players active in 2023 or later (none for anyone
    whose last season is 2022 or earlier), so the club timeline joins three sources: transfers (exact dates),
    appearances (2012 onwards, the club per game) and valuations (dated club *names*: the valuations' club id is
    today's club, not the club on the day, so names are mapped to ids by matching them with appearances)."""

    by_name: dict[str, list[int]]
    dob: dict[int, pd.Timestamp]
    peak: dict[int, float]
    last_season: dict[int, float]
    days: dict[int, np.ndarray]  # observation dates (int days since epoch), sorted
    clubs: dict[int, list[str]]  # club at each observation ("0" = without a club)
    loans: dict[int, list[tuple[pd.Timestamp, str]]]  # fee-less transfers: (date, club he came from)
    club_names: dict[str, str]  # valuations' normalised club name -> club id

    @classmethod
    def build(cls, t: dict[str, pd.DataFrame], gaz: Gazetteer | None = None) -> People:
        players = t["players"]
        by_name: dict[str, list[int]] = defaultdict(list)
        for pid, name in zip(players.player_id, players.name, strict=True):
            if isinstance(name, str):
                by_name[person_key(name)].append(int(pid))
        obs, names = club_timeline(t, gaz)
        days, clubs = {}, {}
        for pid, g in obs.groupby("player_id", sort=False):
            days[int(pid)] = g.day.to_numpy()
            clubs[int(pid)] = g.club.tolist()
        tr = t["transfers"]
        tr = tr[
            (pd.to_datetime(tr.transfer_date) <= TM_SNAPSHOT)
            & (pd.to_numeric(tr.transfer_fee, errors="coerce").fillna(0) == 0)
        ]
        loans: dict[int, list] = defaultdict(list)
        for pid, d, frm in zip(tr.player_id, pd.to_datetime(tr.transfer_date), tr.from_club_id, strict=True):
            if (c := _cid(frm)) is not None:
                loans[int(pid)].append((d, c))
        idx = players.set_index("player_id")
        return cls(
            by_name=dict(by_name),
            dob=pd.to_datetime(idx.date_of_birth).to_dict(),
            peak=idx.highest_market_value_in_eur.fillna(0).to_dict(),
            last_season=idx.last_season.to_dict(),
            days=days,
            clubs=clubs,
            loans=dict(loans),
            club_names=names,
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
        """(club, club that may own him while he is on loan, or None) on day d."""
        if pid not in self.days:
            return None, None
        i = int(np.searchsorted(self.days[pid], _day(d), side="right")) - 1
        if i < 0:
            return None, None
        club = self.clubs[pid][i]
        # a fee-less move in the last 13 months may be a loan: the club he came from may still own him
        parent = next(
            (c for w, c in reversed(self.loans.get(pid, [])) if w <= d and (d - w).days < 400), None
        )
        return (None if club == "0" else club), parent

    def arrivals(self) -> pd.DataFrame:
        """(player, club, date) each time the timeline shows him at a new club; the date is the first sighting."""
        rows = []
        for pid, ds in self.days.items():
            cs = self.clubs[pid]
            for i in range(1, len(cs)):
                if cs[i] != cs[i - 1] and cs[i] != "0":
                    rows.append((str(pid), cs[i], ds[i]))
        out = pd.DataFrame(rows, columns=["tm_player_id", "tm_club_id", "day"])
        out["date"] = pd.to_datetime(out.pop("day"), unit="D")
        return out


def _day(d: pd.Timestamp) -> int:
    return int((pd.Timestamp(d).normalize() - EPOCH).days)


EPOCH = pd.Timestamp("1970-01-01")
WITHOUT_CLUB = {"without club", "retired", "unknown", "career break", "ban", "free agent"}


def club_timeline(
    t: dict[str, pd.DataFrame], gaz: Gazetteer | None = None
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Dated club observations per player (player_id, day, club), sorted, and the club-name map; see People."""
    parts = []
    tr = t["transfers"]
    tr = tr[pd.to_datetime(tr.transfer_date) <= TM_SNAPSHOT]
    d = pd.to_datetime(tr.transfer_date)
    parts.append(
        pd.DataFrame({"player_id": tr.player_id, "date": d, "club": tr.to_club_id.map(_cid), "rank": 0})
    )
    # the club he left was his club until the day before
    parts.append(
        pd.DataFrame(
            {
                "player_id": tr.player_id,
                "date": d - pd.Timedelta(days=1),
                "club": tr.from_club_id.map(_cid),
                "rank": 0,
            }
        )
    )
    if "appearances" in t:
        ap = t["appearances"]
        if "competitions" in t and "competition_id" in ap:  # national-team games are not a club
            nat = t["competitions"].loc[
                t["competitions"].type == "national_team_competition", "competition_id"
            ]
            ap = ap[~ap.competition_id.isin(set(nat))]
        ap = ap[["player_id", "date", "player_club_id"]].copy()
        ap["date"] = pd.to_datetime(ap.date)
        ap["half"] = ap.date.dt.year * 2 + (ap.date.dt.month >= 7)
        g = ap.groupby(["player_id", "player_club_id", "half"]).date.agg(["min", "max"]).reset_index()
        for col in ("min", "max"):
            parts.append(
                pd.DataFrame(
                    {"player_id": g.player_id, "date": g[col], "club": g.player_club_id.map(_cid), "rank": 1}
                )
            )
        names = valuation_club_ids(t, ap, gaz)
    else:
        names = {}
    v = t["player_valuations"][["player_id", "date", "current_club_name"]].copy()
    v["date"] = pd.to_datetime(v.date)
    key = v.current_club_name.fillna("").map(normalise)
    v["club"] = key.map(names)
    v.loc[key.isin(WITHOUT_CLUB), "club"] = "0"
    parts.append(v.dropna(subset=["club"])[["player_id", "date", "club"]].assign(rank=2))
    obs = pd.concat(parts).dropna(subset=["club"])
    obs = obs[obs.date <= TM_SNAPSHOT]
    obs["day"] = (obs.date - EPOCH).dt.days
    # on the same day the more exact source wins
    obs = obs.sort_values(["player_id", "day", "rank"]).drop_duplicates(["player_id", "day"], keep="first")
    return obs[["player_id", "day", "club"]].reset_index(drop=True), names


def valuation_club_ids(t: dict[str, pd.DataFrame], ap: pd.DataFrame, gaz: Gazetteer | None) -> dict[str, str]:
    """Normalised club name as valuations print it -> club id, learnt from the club he played for that month."""
    v = t["player_valuations"][["player_id", "date", "current_club_name"]].copy()
    v["date"] = pd.to_datetime(v.date)
    a = ap[["player_id", "date", "player_club_id"]].sort_values("date")
    m = pd.merge_asof(
        v.sort_values("date"),
        a,
        on="date",
        by="player_id",
        direction="nearest",
        tolerance=pd.Timedelta(days=45),
    ).dropna(subset=["player_club_id"])
    m["key"] = m.current_club_name.fillna("").map(normalise)
    votes = m.groupby(["key", "player_club_id"]).size().rename("n").reset_index()
    total = votes.groupby("key").n.transform("sum")
    best = votes[(votes.n >= 3) & (votes.n / total >= 0.6)].sort_values("n").groupby("key").tail(1)
    out = {k: _cid(c) for k, c in zip(best.key, best.player_club_id, strict=True)}
    for r in t["clubs"].itertuples():  # clubs in the covered leagues by their full name
        out.setdefault(normalise(r.name), str(r.club_id))
    if gaz is not None:  # anything else by the gazetteer (short names)
        for name in set(v.current_club_name.dropna().map(normalise)) - set(out):
            if name and name not in WITHOUT_CLUB and (cid := gaz.names.get(name)):
                out[name] = cid
    return out


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
        # older columns put clubs in bold too: a bold name that is a club is not a player
        names = [n for n in r.players if n and normalise(n) not in gaz.names]
        stats["bold_clubs"] += len(r.players) - len(names)
        sentences = SENTENCE.split(r.body_text)
        # take the players' own names out of the text so a surname is never read as a club
        strip = sorted(
            {n for n in names} | {n.split()[-1] for n in names if len(n.split()[-1]) >= 4},
            key=len,
            reverse=True,
        )
        pattern = (
            re.compile(r"\b(?:" + "|".join(re.escape(s) for s in strip) + r")\b", flags=re.I)
            if strip
            else None
        )
        all_clubs = {c for c, _ in gaz.find(pattern.sub(" ; ", r.body_text) if pattern else r.body_text)}
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
            clubs = gaz.find(pattern.sub(" ; ", text) if pattern else text)
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


def all_moves(people: People, wiki: pd.DataFrame | None, gaz: Gazetteer) -> pd.DataFrame:
    """Every recorded arrival (player, club, date): Transfermarkt's timeline to its snapshot, then Wikipedia's lists."""
    out = people.arrivals()
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


def club_values(valuations: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    """Squad value (sum of the latest valuation in the year before) per club on 1 January and 1 July each year.
    The club is the one the valuation names on its date (its club id column is today's club)."""
    v = valuations[["player_id", "date", "market_value_in_eur", "current_club_name"]].copy()
    v["date"] = pd.to_datetime(v.date)
    v["club"] = v.current_club_name.fillna("").map(normalise).map(names)
    v = v.dropna(subset=["club"])
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
            tot = w.groupby("club").market_value_in_eur.sum()
            rows.append(pd.DataFrame({"snap": s, "club": tot.index, "value": tot.to_numpy()}))
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
    x["modern_column"] = (x.season >= MODERN_SEASON).astype(float)
    return x


# ---- estimates ---------------------------------------------------------------------------------------------


MAX_PRIOR_STRENGTH = (
    5000.0  # a + b: when outlets are indistinguishable the likelihood keeps rising towards infinity
)


def beta_prior(hits: np.ndarray, n: np.ndarray) -> tuple[float, float]:
    """Beta(a, b) maximising the beta-binomial marginal likelihood of the outlets' records, as (mean, strength)."""

    def nll(z):
        m, k = expit(z[0]), min(np.exp(z[1]), MAX_PRIOR_STRENGTH)
        a, b = m * k, (1 - m) * k
        return -np.sum(betaln(hits + a, n - hits + b) - betaln(a, b))

    p = float(np.clip(hits.sum() / max(n.sum(), 1), 1e-3, 1 - 1e-3))
    res = minimize(nll, [logit(p), np.log(20.0)], method="Nelder-Mead")
    m, k = expit(res.x[0]), min(float(np.exp(res.x[1])), MAX_PRIOR_STRENGTH)
    return float(m * k), float((1 - m) * k)


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
    return np.round(logit(np.clip(p, 1e-3, 1 - 1e-3)), 3)  # rounded: tiny differences carry no signal


@dataclass
class RumourModel:
    rates: dict
    prior: tuple[float, float]
    scaler: StandardScaler
    clf: LogisticRegression
    medians: dict[str, float]
    features: list[str]

    @classmethod
    def fit(cls, train: pd.DataFrame, y: str, features: list[str] | None = None) -> RumourModel:
        features = features or FEATURES
        rates, prior = outlet_rates(train, y)
        # the outlet feature on a training row comes from the outlets' records in the *other* seasons, never its
        # own (leave-one-out within a season leaks the row's label once the scaler magnifies tiny differences)
        x = train.copy()
        x["outlet_logit"] = 0.0
        for season in x.season.unique() if "outlet_logit" in features else ():
            other = x.season != season
            r_, p_ = outlet_rates(x[other], y)
            x.loc[~other, "outlet_logit"] = _outlet_logit(x[~other], r_, p_)
        medians = {f: float(x[f].median()) for f in features}
        X = x[features].fillna(medians).to_numpy(float)
        scaler = StandardScaler().fit(X)
        clf = LogisticRegression(C=1.0, max_iter=2000).fit(scaler.transform(X), x[y].astype(int))
        return cls(rates, prior, scaler, clf, medians, list(features))

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        x = x.copy()
        x["outlet_logit"] = _outlet_logit(x, self.rates, self.prior)
        X = x[self.features].fillna(self.medians).to_numpy(float)
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
    t = {
        n: pd.read_parquet(tm_dir / f"{n}.parquet")
        for n in ("players", "transfers", "player_valuations", "clubs")
    }
    t["appearances"] = pd.read_parquet(
        tm_dir / "appearances.parquet", columns=["player_id", "date", "player_club_id", "competition_id"]
    )
    t["competitions"] = pd.read_parquet(tm_dir / "competitions.parquet", columns=["competition_id", "type"])
    return t


def build(
    con: duckdb.DuckDBPyConnection, t: dict[str, pd.DataFrame] | None = None
) -> tuple[pd.DataFrame, dict]:
    """All links with labels and features, plus match statistics."""
    t = t or load_tm()
    rumours = load_rumours(con)
    wiki = con.execute("SELECT * FROM wiki_transfers").df() if _has(con, "wiki_transfers") else None
    gaz = Gazetteer.build(t["transfers"], t["clubs"])
    people = People.build(t, gaz)
    links, stats = links_from(rumours, gaz, people)
    stats["paragraphs"] = len(rumours)
    stats["paragraphs_linked"] = int(links.rumour_id.nunique()) if len(links) else 0
    cov = coverage(wiki)
    links = label(links, all_moves(people, wiki, gaz), cov)
    links = features(links, t, club_values(t["player_valuations"], people.club_names))
    stats["coverage_end"] = str(cov.date())
    return links, stats


def load_rumours(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """The archive plus the nightly Rumour Mill items (news_items, same ids), so this week's rumours are scored
    even before the weekly archive run picks them up."""
    return con.execute(
        """SELECT rumour_id, published_at, players, outlet, body_text FROM rumours
           UNION ALL
           SELECT news_id, published_at, string_split(title, '; '), outlet, body_text FROM news_items
           WHERE source = 'guardian_rumours' AND body_text IS NOT NULL
             AND news_id NOT IN (SELECT rumour_id FROM rumours)"""
    ).df()


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
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
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
        recent = np.full(len(test), train[train.season >= MODERN_SEASON][y].mean())
        p_model, p_outlet = model.predict(test), model.outlet_p(test)
        variants = {
            "with the outlet record": RumourModel.fit(train, y, [*OUTLET_FEATURES, *FEATURES]),
            "without the era flag": RumourModel.fit(train, y, [f for f in FEATURES if f != "modern_column"]),
            "without repeats": RumourModel.fit(train, y, [f for f in FEATURES if f != "log_repeats"]),
            f"trained on {MODERN_SEASON}-{MODERN_SEASON + 1 - 2000:02d} onwards only": RumourModel.fit(
                train[train.season >= MODERN_SEASON], y
            ),
        }
        by_season = lab.groupby("season")[y].agg(["mean", "count"])
        report["horizons"][str(n)] = {
            "train_seasons": f"{int(train.season.min())}-{int(train.season.max()) + 1 - 2000:02d}",
            "test_season": f"{test_season}-{test_season + 1 - 2000:02d}",
            "n_train": len(train),
            "n_test": len(test),
            "train_rate": float(train[y].mean()),
            "test_rate": float(yt.mean()),
            "base_rate": scores(base, yt),
            "recent_rate": scores(recent, yt),
            "outlet_only": scores(p_outlet, yt),
            "model": scores(p_model, yt),
            "variants": {k: scores(m.predict(test), yt) for k, m in variants.items()},
            "calibration": calibration(p_model, yt),
            "coefficients": dict(zip(model.features, np.round(model.clf.coef_[0], 3).tolist(), strict=True)),
            "recent_train_rate": float(train[train.season >= MODERN_SEASON][y].mean()),
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

    def pct(a: float, b: float) -> str:
        return f"{a / b:.0%}" if b else "-"

    def row(name: str, sc: dict) -> str:
        auc = f"{sc['auc']:.3f}" if "auc" in sc else "-"
        return f"| {name} | {sc['brier']:.4f} | {sc['log_loss']:.4f} | {auc} | {sc['mean_p']:.1%} |"

    lines = [
        "# Transfer rumour track record backtest",
        "",
        f"Generated {r['generated_at']}. Rumours: the Guardian's Rumour Mill archive (Guardian Open Platform), every",
        "column since February 2001. What happened: transfermarkt-datasets (CC0) to 2026-07-06, then Wikipedia's",
        f"summer-2026 transfer lists (CC BY-SA) to {s.get('coverage_end')}. A rumour *came true* when the player",
        "joined the club it linked him with within the horizon; links whose horizon is not over are left out.",
        "",
        "## Matching",
        "",
        f"- Paragraphs naming someone in bold: {s.get('paragraphs', 0)}. Bold names that are clubs (older columns"
        f" bold clubs too): {s.get('bold_clubs', 0)}; other names: {s.get('names', 0)}.",
        f"- Names matched to a Transfermarkt player active at the time: {s.get('names_matched', 0)}"
        f" ({pct(s.get('names_matched', 0), s.get('names', 0))}); {s.get('names_ambiguous', 0)} had namesakes"
        " (resolved by the club the item mentions, then by peak value). Unmatched: managers, nicknames, surnames"
        " only, and players the dataset does not cover (few before 2004).",
        f"- Matched players with no other club named near them: {s.get('players_without_club', 0)}.",
        f"- Links (rumour x player x club, the player's own club and loan owner excluded): {s.get('links', 0)},"
        f" from {s.get('paragraphs_linked', 0)} paragraphs ({pct(s.get('paragraphs_linked', 0), s.get('paragraphs', 0))}).",
        "- Where a player played when: transfermarkt-datasets carries transfer histories only for players active",
        "  in 2023 or later, so clubs on a date come from transfers, appearances (2012 onwards) and the valuations'",
        "  dated club names together (the valuations' club id is today's club, not the club on the day).",
        "",
        "## Results",
        "",
        "Train on every season from 2004-05 up to the test season; test on the latest season with labels.",
        "",
    ]
    for n, h in r["horizons"].items():
        lines += [
            f"### Within {n} months: test {h['test_season']}, {h['n_test']} links, {h['test_rate']:.1%} came true",
            "",
            f"Training {h['train_seasons']}: {h['n_train']} links, {h['train_rate']:.1%} came true"
            f" ({h['recent_train_rate']:.1%} from {MODERN_SEASON}-{MODERN_SEASON + 1 - 2000:02d}).",
            "",
            "| Forecast | Brier | Log loss | AUC | Mean forecast |",
            "|---|---|---|---|---|",
            row("Base rate, all training seasons", h["base_rate"]),
            row(f"Base rate since {MODERN_SEASON}-{MODERN_SEASON + 1 - 2000:02d}", h["recent_rate"]),
            row("Outlet record only (shrunk)", h["outlet_only"]),
            row("**Shipped: logistic**", h["model"]),
            *[row(k, v) for k, v in h["variants"].items()],
            "",
        ]
    h = r["horizons"][str(SHIPPED_HORIZON)]
    lines += [
        f"## Shipped: {SHIPPED_HORIZON} months",
        "",
        "Calibration on the test season:",
        "",
        "| Forecast | Links | Mean forecast | Came true |",
        "|---|---|---|---|",
        *[f"| {c['bin']} | {c['n']} | {c['mean_p']:.1%} | {c['rate']:.1%} |" for c in h["calibration"]],
        "",
        "Coefficients (standardised features): "
        + ", ".join(f"{k} {v}" for k, v in h["coefficients"].items()),
        "",
        f"Outlet records, shrunk to the overall rate with a Beta({h['prior']['a']:.1f}, {h['prior']['b']:.1f}) prior"
        " (fitted by maximum marginal likelihood): outlets with 20+ training links.",
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
        "## Reading this honestly",
        "",
        "- **Outlets barely differ (a negative result).** The fitted prior is strong, so even outlets with hundreds",
        "  of links are shrunk close to the average, and the outlet-only forecast is no better than the base rate.",
        "  Adding the outlet's record to the rumour model made both validation seasons (2023-24, 2024-25) and the",
        "  test season slightly worse, so it is not used to score rumours; the records are published as they are.",
        "  Most items (the column's own summaries) credit no outlet; outlets are known only when the column links",
        "  to or names them. What separates rumours is the rumour itself: how often the same link was reported",
        "  before, how many clubs the item names, the transfer window, and how valuable the player is.",
        f"- **The column changed.** From {MODERN_SEASON}-{MODERN_SEASON + 1 - 2000:02d} it runs fewer, shorter items"
        " and about twice as many come true; a base rate from all years badly underestimates today's rumours. The era flag handles",
        "  it; the base rate since then is the fair baseline to beat.",
        "- **Labels are a lower bound.** A move counts only if it is in transfermarkt-datasets or, after July 2026,",
        "  on one of the Wikipedia lists (13 European leagues: moves to Saudi Arabia, the United States or Portugal",
        "  in summer 2026 are missed). Dates from appearances or valuations are first sightings, so a move can look",
        "  later than it was. Links come from club names near the player's name; some are noise (a club mentioned",
        "  for another reason), which also pulls the rates down. Before 2012-13 there are no appearances, so club",
        "  timelines rest on transfers and valuations alone and the earlier seasons' rates are probably understated.",
        "- **Calibration is rough at the edges:** the lowest forecasts came true less often than forecast and the",
        "  few highest ones more often (table above); the middle, where most rumours sit, is close.",
        "- **Not used:** contract length (only today's contracts exist; for old rumours they leak the outcome).",
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
    entity_id '<paragraph news_id>:<folded player name>' and context = the dugout club the item links him with
    (metrics p_true, one_in). Also rewrites the rumour_links table."""
    from dugout_models.publish import _row

    if not (TM_DIR / "transfers.parquet").exists() or not len(load_rumours(con)):
        log.warning("rumours: no archive or no transfermarkt-datasets tables yet (history job); skipped")
        return []
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
            # one entity per player named in the paragraph; context = the dugout club he is linked with
            eid, ctx = f"{r.rumour_id}:{fold(r.player_name)}", slugs[r.tm_club_id]
            rows.append(_row("rumours", "rumour", eid, "p_true", float(r.p), now, ctx))
            rows.append(
                _row(
                    "rumours",
                    "rumour",
                    eid,
                    "one_in",
                    int(max(1, round(1 / max(r.p, 1e-3)))),
                    now,
                    ctx,
                )
            )
    return rows
