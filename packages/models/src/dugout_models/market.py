"""Transfer market outputs for `publish`: fair value, sale likelihood and a scouting shortlist per club.

Built from transfermarkt-datasets (CC0; valuations and appearances run to June 2026) plus the committed current
squad file `data/reference/transfermarkt_squads.csv` (Premier League values and contracts as of the last local
refresh) and, for leagues without FPL, the football-data.org squads in the warehouse (`warehouse_squads`), valued at
their latest transfermarkt-datasets valuation.

  fair_fee              valuation model (docs/backtests/valuation.md): price if sold to another club in the same
                        league (the model is fitted on buyers in every league; Premier League buyers are a segment)
  sale_likelihood       mobility model (realism.md): P(sold for a fee in a transfer window), features as of today
  needs                 positions where the club is thinnest/weakest (heuristic: depth and value at the position)
  shortlist             three lists per need, from first-tier players in every league the dataset covers.
                        best: highest market value among players the playing-time model (opportunity.md) expects
                        to get at least 40% of the club's minutes, whatever the cost or the odds.
                        realistic: ranked by realism = P(move to this club in a window) = P(sold) x P(this buyer |
                        sold) (realism.md), among players whose club is poorer than the buyer, is more likely than
                        average to sell them and is not a top-ten-in-Europe club starting them, and who would be an
                        upgrade on the club's last regular starter at the position.
                        gem (lookup.py): ages 17-23, playing regularly, cheaper than that starter today, ranked by
                        the potential model's projected value in four years.
                        The ranking rules are transparent; each component is a backtested model.
  sale_likelihood is not scored for players who joined their club in the last 12 months (no data at the new club).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
from dugout_pipeline.settings import REPO_ROOT
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from dugout_models import realism, valuation
from dugout_models.opportunity import FEATURES as OPP_FEATURES
from dugout_models.opportunity import OpportunityModel
from dugout_models.opportunity import build as opportunity_frame
from dugout_models.positions import positions_played
from dugout_models.publish import _row

log = logging.getLogger(__name__)

SQUADS = REPO_ROOT / "data" / "reference" / "transfermarkt_squads.csv"
PL = "GB1"
# Position groups a club needs covered, as transfermarkt sub-positions.
GROUPS = {
    "goalkeeper": ["Goalkeeper"],
    "centre-back": ["Centre-Back"],
    "full-back": ["Right-Back", "Left-Back"],
    "defensive midfield": ["Defensive Midfield"],
    "central midfield": ["Central Midfield", "Attacking Midfield"],
    "winger": ["Right Winger", "Left Winger", "Right Midfield", "Left Midfield"],
    "centre-forward": ["Centre-Forward", "Second Striker"],
}
NEEDED = {
    "goalkeeper": 2,
    "centre-back": 4,
    "full-back": 4,
    "defensive midfield": 2,
    "central midfield": 4,
    "winger": 4,
    "centre-forward": 2,
}
# regular starters per position group in a typical XI: the bar a realistic target has to clear
STARTERS = {
    "goalkeeper": 1,
    "centre-back": 2,
    "full-back": 2,
    "defensive midfield": 1,
    "central midfield": 2,
    "winger": 2,
    "centre-forward": 1,
}
SHORTLIST = 5
REGULAR_SHARE = 0.4  # expected share of the club's minutes to count as a regular (best-fit list)


def _season(d: pd.Series) -> pd.Series:
    return pd.Series(np.where(d.dt.month >= 7, d.dt.year, d.dt.year - 1), index=d.index)


# Official club announcements of agreed signings, e.g. "Liverpool agree deal to sign goalkeeper Lucca Brughmans".
_SIGNING = re.compile(
    r"\b(agree|agreed|complete|completes|seal|seals)\b.*\b(deal|sign|signing|transfer)\b", re.I
)
_OTHER_TEAMS = re.compile(r"\bwomen\b|lfc-women|\bu-?(?:18|19|21|23)s?\b|academy|under-(?:18|21)", re.I)
POSITION_WORDS = {
    "goalkeeper": r"goalkeeper|keeper|stopper",
    "centre-back": r"centre-back|center-back|central defender|centre-half",
    "full-back": r"full-back|right-back|left-back|wing-back",
    "defensive midfield": r"defensive midfielder|holding midfielder",
    "central midfield": r"midfielder|playmaker",
    "winger": r"winger|wide forward",
    "centre-forward": r"striker|centre-forward|center-forward|forward",
}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def agreed_signings(
    con: duckdb.DuckDBPyConnection, now: datetime, squad_names: set[str] | None = None, days: int = 120
) -> dict[str, list[tuple]]:
    """club -> [(position group, title, url)]: incoming signings the club itself has announced (tier 1 club_news)
    who are not in the current squad yet (e.g. loaned back, joining next summer).

    Only titles that name the club as the buyer count ("Liverpool agree deal to sign ..."); departures ("X completes
    transfer to Y"), women's team and academy news, and players already in the squad list are skipped."""
    names = dict(con.execute("SELECT club_id, name FROM clubs").fetchall())
    surnames = {_fold(n.split()[-1]) for n in (squad_names or set()) if isinstance(n, str) and n.split()}
    rows = con.execute(
        """SELECT club_ids, title, url, body_text FROM news_items WHERE source = 'club_news' AND source_tier = 1
           AND published_at >= ?""",
        [now - pd.Timedelta(days=days)],
    ).fetchall()
    out: dict[str, list[tuple]] = {}
    for clubs, title, url, body in rows:
        if not title or not clubs or not _SIGNING.search(title):
            continue
        club = clubs[0]  # the site's own club comes first
        short = (names.get(club) or club).split()[0]
        buyer = re.search(
            rf"\b{re.escape(short)}\b.*\b(agree|sign|complete)|\bjoins? {re.escape(short)}\b"
            rf"|completes {re.escape(short)}\b",
            title,
            re.I,
        )
        outgoing = re.search(r"\b(transfer|move|loan) to\b|\bleaves?\b|\bdeparts?\b", title, re.I)
        if not buyer or outgoing:
            continue
        head = f"{title} {(body or '')[:300]}"
        if _OTHER_TEAMS.search(f"{url} {head}"):
            continue  # women's team, academy and youth signings do not change the first-team squad
        if any(re.search(rf"\b{re.escape(sn)}\b", _fold(title)) for sn in surnames):
            continue  # already in the squad list: counted as a current player
        group = next((g for g, w in POSITION_WORDS.items() if re.search(w, head, re.I)), None)
        if group:
            out.setdefault(club, []).append((group, title, url))
    return out


def warehouse_squads(con: duckdb.DuckDBPyConnection, skip: set[str]) -> pd.DataFrame:
    """Squads of the clubs the committed squad file does not cover (leagues without FPL), from the warehouse:
    football-data.org squads with Transfermarkt ids (pipeline matching), the club's league competition id from
    config, and the date a player joined from Wikipedia's transfer lists where it shows this summer's move.
    Values, contracts and earlier join dates are filled from transfermarkt-datasets by `Market`."""
    from dugout_pipeline.clubs import league_config, leagues

    comps = {lg: league_config(lg)["provider_ids"].get("transfermarkt") for lg in leagues()}
    try:
        joined = """(SELECT max(date) FROM wiki_transfers w WHERE w.tm_player_id = e.external_id
                     AND w.tm_to_club = ec.external_id AND w.kind <> 'end_of_loan')"""
        con.execute("SELECT 1 FROM wiki_transfers LIMIT 0")
    except duckdb.CatalogException:  # warehouse from before the table existed
        joined = "NULL"
    df = con.execute(
        f"""SELECT e.external_id AS tm_player_id, p.player_id, p.name, ec.external_id AS tm_club_id, p.club_id,
                   CAST(p.birth_date AS VARCHAR) AS birth_date, CAST({joined} AS VARCHAR) AS joined, c.league,
                   c.name AS club_name
            FROM players p JOIN clubs c USING (club_id)
            JOIN entity_ids e ON e.entity_type = 'player' AND e.provider = 'transfermarkt'
                 AND e.namespace = 'spieler' AND e.dugout_id = p.player_id
            JOIN entity_ids ec ON ec.entity_type = 'club' AND ec.provider = 'transfermarkt'
                 AND ec.namespace = 'verein' AND ec.dugout_id = c.club_id
            WHERE NOT list_contains(?, c.club_id)""",
        [sorted(skip)],
    ).df()
    df["comp"] = df.league.map(comps)
    return df.dropna(subset=["comp"]).drop(columns="league").drop_duplicates("tm_player_id")


class Market:
    def __init__(self, now: datetime, extra_squads: pd.DataFrame | None = None):
        self.now = pd.Timestamp(now.date())
        self.t = valuation.load()
        comps = pd.read_parquet(valuation.TM_DIR / "competitions.parquet")
        self.comps = comps
        realism.TOP_TIER.update(comps.loc[comps.sub_type == "first_tier", "competition_id"])
        # the committed squad file (Premier League, refreshed locally) plus squads of other leagues (warehouse);
        # `comp` is each club's Transfermarkt competition: buyer-league features use it
        self.squads = pd.read_csv(SQUADS, dtype={"tm_player_id": str, "tm_club_id": str}).assign(comp=PL)
        self.incoming: dict[str, list[tuple]] = {}  # filled from official announcements by market()
        self.moved: set[str] = set()  # moved since the dataset snapshot (Wikipedia lists), filled by market()
        self.played = positions_played()  # positions players actually start at (positions.py)
        self.players = self.t["players"].copy()
        self.players["player_id"] = self.players.player_id.astype(str)
        self.players = self.players.set_index("player_id")
        if extra_squads is not None and len(extra_squads):
            self.squads = pd.concat([self.squads, self._fill(extra_squads)], ignore_index=True)
        self._with_current_values()
        self.clubs = self.t["clubs"].copy()
        self.clubs["club_id"] = self.clubs.club_id.astype(str)

    def _fill(self, sq: pd.DataFrame) -> pd.DataFrame:
        """Values (latest valuation, June at the latest), contract expiry and join date from transfermarkt-datasets
        for squads without a squad-file row. No scrape date: these rows add no valuation rows of their own."""
        sq = sq.copy()
        v = self.t["player_valuations"].assign(player_id=lambda d: d.player_id.astype(str))
        latest = v.sort_values("date").groupby("player_id").market_value_in_eur.last()
        sq["market_value_eur"] = sq.tm_player_id.map(latest)
        sq["contract_expires"] = pd.to_datetime(
            sq.tm_player_id.map(self.players.contract_expiration_date), errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        tr = self.t["transfers"].assign(
            player_id=lambda d: d.player_id.astype(str), to_club_id=lambda d: d.to_club_id.astype(str)
        )
        last_in = tr.groupby(["player_id", "to_club_id"]).transfer_date.max()
        earlier = [last_in.get((p, c)) for p, c in zip(sq.tm_player_id, sq.tm_club_id, strict=True)]
        sq["joined"] = sq.joined.where(sq.joined.notna(), pd.Series(earlier, index=sq.index))
        sq["joined"] = pd.to_datetime(sq.joined, errors="coerce").dt.strftime("%Y-%m-%d")
        return sq

    def club_names(self) -> pd.Series:
        """Transfermarkt club id -> name: the dataset's, else the warehouse's (a club new to the top flight)."""
        names = self.t["clubs"].assign(club_id=lambda d: d.club_id.astype(str)).set_index("club_id").name
        if "club_name" in self.squads:
            extra = self.squads.dropna(subset=["club_name"]).drop_duplicates("tm_club_id")
            names = pd.concat(
                [names, extra.set_index("tm_club_id").club_name[lambda s: ~s.index.isin(names.index)]]
            )
        return names

    def buyer_comp(self, tm_club: str | None) -> str:
        """The buyer's league: from the squads (current) or the dataset; the Premier League when unknown."""
        if tm_club is None:
            return PL
        row = self.squads.loc[self.squads.tm_club_id == str(tm_club), "comp"]
        return row.iloc[0] if len(row) else (self.comp_of(str(tm_club)) or PL)

    def _with_current_values(self) -> None:
        """Adds today's squad-file values as valuation rows, so 'latest value' means October, not June."""
        v = self.t["player_valuations"].copy()
        v["player_id"] = v.player_id.astype(str)
        v["current_club_id"] = v.current_club_id.astype(str)
        sq = self.squads.dropna(subset=["market_value_eur", "scraped_on"])
        extra = pd.DataFrame(
            {
                "player_id": sq.tm_player_id,
                "date": pd.to_datetime(sq.scraped_on),
                "market_value_in_eur": sq.market_value_eur.astype(float),
                "current_club_name": sq.club_id,
                "current_club_id": sq.tm_club_id,
                "player_club_domestic_competition_id": sq.comp,
            }
        )
        v["date"] = pd.to_datetime(v.date)
        self.t["player_valuations"] = pd.concat([v, extra], ignore_index=True)
        for k in ("transfers", "appearances"):
            df = self.t[k].copy()
            for c in ("player_id", "from_club_id", "to_club_id", "player_club_id"):
                if c in df:
                    df[c] = df[c].astype(str)
            self.t[k] = df
        self.t["players"]["player_id"] = self.t["players"].player_id.astype(str)
        self.t["clubs"]["club_id"] = self.t["clubs"].club_id.astype(str)

    # -- fitted models ---------------------------------------------------------------------------------------
    def frame(self, name: str, build, **params) -> pd.DataFrame:
        """Training frames depend only on the dataset files: cached on disk between runs."""
        from dugout_models.cache import cached_frame

        return cached_frame(name, params, [valuation.TM_DIR, SQUADS], build)

    def fit(self) -> None:
        df = self.frame("valuation", lambda: valuation.build(self.t), features=valuation.FEATURES)
        self.val = valuation.ValuationModel().fit(df[df.date <= self.now])
        raw, people = None, None

        # The realism training frames are built from freshly loaded tables, exactly as their backtest builds them:
        # self.t carries string ids (_with_current_values) while the club timeline uses integer ones, which left
        # the destination pairs empty. self.t's extra rows are today's squad values, outside the 2015-2025 years.
        def tables():
            nonlocal raw
            raw = raw or valuation.load()
            return raw

        def timeline():  # built once, only when a cached frame has to be rebuilt
            nonlocal people
            people = people or realism.club_people(tables())
            return people

        snap = self.frame(
            "mobility",
            lambda: realism.snapshots(tables(), range(2015, 2026), people=timeline()),
            years=[2015, 2025],
            features=realism.MOBILITY,  # a feature change rebuilds the cached frame
            clubs="dated",  # the club on 1 June from the club timeline (realism.md, 2026-10-17)
            covered_only=realism.COVERED_ONLY,
        )
        snap = snap.dropna(subset=realism.MOBILITY)
        self.mob = realism.mobility_model().fit(snap[realism.MOBILITY], snap.moved)
        self.mob_base = float(snap.moved.mean())
        pairs = self.frame(
            "destination",
            lambda: realism.destination_pairs(tables(), range(2015, 2026), people=timeline()),
            years=[2015, 2025],
            features=realism.DESTINATION,
            clubs="dated",
        )
        pairs = pairs.dropna(subset=realism.DESTINATION)
        self.dest = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(
            pairs[realism.DESTINATION], pairs.label
        )
        self.dest_base = float(pairs.label.mean())
        self.opp = OpportunityModel().fit(self.frame("opportunity", opportunity_frame))
        self.now_snap = realism.snapshots(self.t, [self.now.year], as_of=self.now).set_index("player_id")
        self.league_value = valuation.league_strength(self.t).set_index(["comp", "year"]).league_value
        vals = self.t["player_valuations"]
        recent = (
            vals[vals.date >= self.now - pd.Timedelta(days=365)]
            .sort_values("date")
            .groupby("player_id")
            .tail(1)
        )
        self.club_value = recent.groupby("current_club_id").market_value_in_eur.sum()
        self.latest_value = vals.sort_values("date").groupby("player_id").market_value_in_eur.last()
        tr = self.t["transfers"].copy()
        tr["date"] = pd.to_datetime(tr.transfer_date)
        tr["fee"] = pd.to_numeric(tr.transfer_fee, errors="coerce")
        recent_tr = tr[(tr.date < self.now) & (tr.date >= self.now - pd.Timedelta(days=1095))]
        self.capacity = recent_tr.groupby("to_club_id").fee.max()
        self.buyers = self._buyer_table()
        ap = self.t["appearances"].copy()
        ap["date"] = pd.to_datetime(ap.date)
        ap["season"] = _season(ap.date)
        self.ap = ap

    # -- per-player pieces -----------------------------------------------------------------------------------
    def comp_of(self, tm_club: str) -> str | None:
        row = self.clubs.loc[self.clubs.club_id == str(tm_club), "domestic_competition_id"]
        return row.iloc[0] if len(row) else None

    def fair_fee(
        self,
        ids: list[str],
        seller_comp: list[str | None],
        seller_club: list[str | None] | None = None,
        buyer_club: str | None = None,
        buyer_comp: str | None = None,
    ) -> pd.DataFrame:
        """Fair fee if sold to `buyer_club` (Transfermarkt id), or to a typical club of `buyer_comp` (default: the
        Premier League) when None. The valuation model is fitted on buyers in every league (valuation.md)."""
        to = buyer_comp or self.buyer_comp(buyer_club)
        ap = self.ap[(self.ap.date < self.now) & (self.ap.date >= self.now - pd.Timedelta(days=365))]
        form = ap.groupby("player_id").agg(
            minutes_365=("minutes_played", "sum"),
            goals=("goals", "sum"),
            assists=("assists", "sum"),
            apps_365=("game_id", "nunique"),
        )
        year = self.now.year
        ls_year = max(y for c, y in self.league_value.index if c == PL)
        df = pd.DataFrame({"player_id": ids, "from_comp": seller_comp})
        df["age"] = (self.now - pd.to_datetime(df.player_id.map(self.players.date_of_birth))).dt.days / 365.25
        df["log_mv"] = np.log1p(df.player_id.map(self.latest_value))
        df = df.join(form, on="player_id")
        df[["minutes_365", "goals", "assists", "apps_365"]] = df[
            ["minutes_365", "goals", "assists", "apps_365"]
        ].fillna(0)
        df["ga_p90_365"] = (df.goals + df.assists) / df.minutes_365.clip(lower=90) * 90
        df["log_from_league"] = np.log1p([self.league_value.get((c, ls_year), np.nan) for c in df.from_comp])
        df["log_to_league"] = np.log1p(self.league_value.get((to, ls_year), np.nan))
        df["same_league"] = (df.from_comp == to).astype(float)
        df["year"] = year
        df["mv"] = df.player_id.map(self.latest_value)
        df["to_pl"] = float(to == PL)
        df["pl_to_pl"] = ((df.from_comp == PL) & (to == PL)).astype(float)
        if seller_club is None:  # each player's current club in the dataset
            seller_club = df.player_id.map(self.players.current_club_id).astype(str).tolist()
        df["log_from_cv"] = np.log1p([self.club_value.get(str(c), 0.0) for c in seller_club])
        league_clubs = self.squads.loc[self.squads.comp == to, "tm_club_id"].astype(str).unique()
        if not len(league_clubs):
            league_clubs = self.clubs.loc[self.clubs.domestic_competition_id == to, "club_id"].unique()
        typical = float(np.median([self.club_value.get(c, 0.0) for c in league_clubs]))
        df["log_to_cv"] = np.log1p(self.club_value.get(str(buyer_club), typical) if buyer_club else typical)
        pos = df.player_id.map(self.players.position)
        for p in ("Attack", "Midfield", "Defender", "Goalkeeper"):
            df[f"pos_{p}"] = (pos == p).astype(float)
        ok = df[df.mv > 0].dropna(subset=["age", "log_mv"]).index
        out = pd.DataFrame(index=df.index, columns=["fair_fee", "low", "high"], dtype=float)
        if len(ok):
            out.loc[ok] = self.val.predict(df.loc[ok]).to_numpy()
        return out

    def sale_likelihood(self, ids: list[str]) -> pd.Series:
        snap = self.now_snap.reindex(ids)
        ok = snap.dropna(subset=realism.MOBILITY)
        p = pd.Series(np.nan, index=ids)
        if len(ok):
            p.loc[ok.index] = self.mob.predict_proba(ok[realism.MOBILITY])[:, 1]
        return p

    def _buyer_table(self) -> pd.DataFrame:
        """Every club that paid a fee in the last three years: the plausible buyers a destination competes with."""
        clubs = self.clubs.set_index("club_id")
        country = self.comps.set_index("competition_id").country_name
        cv = np.log1p(self.club_value.reindex(clubs.index).fillna(0))
        league_median = self.club_value.reindex(clubs.index).groupby(clubs.domestic_competition_id).median()
        t = pd.DataFrame(
            {
                "comp": clubs.domestic_competition_id,
                "cap": np.log1p(self.capacity.reindex(clubs.index).fillna(0)),
                "cv": cv,
                "lv": np.log1p(clubs.domestic_competition_id.map(league_median).fillna(0)),
            }
        )
        t["country"] = t.comp.map(country)
        return t

    def realism_score(self, pid: str, seller: str, buyer: str, fee: float) -> dict | None:
        """P(this move happens in a window) = P(sold) x P(this buyer | sold).

        P(this buyer | sold) normalises the pairwise destination model over all plausible buyers (its raw output
        is a 1-in-6 comparison, not a probability over the market). The contract prior shifts the sale odds."""
        p_mob = self.sale_likelihood([pid]).iloc[0]
        bt = self.buyers
        if np.isnan(p_mob) or seller not in bt.index or buyer not in bt.index:
            return None
        cand = bt[(bt.cap > 0) & (bt.index != seller)]
        if buyer not in cand.index:
            cand = pd.concat([cand, bt.loc[[buyer]]])
        s = bt.loc[seller]
        X = pd.DataFrame(
            {
                "capacity_ratio": cand.cap - np.log1p(fee),
                "league_gap": cand.lv - s.lv,
                "club_value_gap": cand.cv - s.cv,
                "same_league": (cand.comp == s.comp).astype(float),
                "same_country": (cand.country == s.country).astype(float),
                "step_down": (s.cv - cand.cv).clip(lower=0),
            }
        )[realism.DESTINATION]
        p = self.dest.predict_proba(X)[:, 1]
        odds = pd.Series(p / (1 - p), index=cand.index)
        share = float(odds[buyer] / odds.sum())
        expiry = pd.to_datetime(self.players.contract_expiration_date.get(pid))
        months = (expiry - self.now).days / 30.44 if pd.notna(expiry) else None
        sold_odds = p_mob / (1 - p_mob) * np.exp(realism.contract_points(months))
        p_sold = sold_odds / (1 + sold_odds)
        return {
            "p_move": float(p_sold * share),
            "p_sold": float(p_sold),
            "p_this_club": share,
            "step_up": float(bt.loc[buyer].cv - s.cv),  # > 0: the buyer is the richer club
            "contract_months": None if months is None else float(months),
        }

    def _last_season(self):
        """(minutes by player, main club by player, games by club) in the last completed season; computed once."""
        if not hasattr(self, "_season_cache"):
            last = self.now.year - 1 if self.now.month >= 7 else self.now.year - 2
            season = self.ap[self.ap.season == last]
            self._season_cache = (
                season.groupby("player_id").minutes_played.sum(),
                season.groupby("player_id").player_club_id.agg(lambda s: s.mode().iloc[0]),
                season.groupby("player_club_id").date.nunique(),
            )
        return self._season_cache

    def outlook(self, pid: str, buyer: str, fee: float, incumbents: pd.DataFrame) -> float | None:
        """Expected minutes share at `buyer` (opportunity model) given the current squad at his position."""
        mins, main, games_by_club = self._last_season()
        if pid not in mins.index:
            return None
        club = main[pid]
        games = games_by_club.get(club, 0)
        mv = self.latest_value.get(pid, np.nan)
        dob = pd.to_datetime(self.players.date_of_birth.get(pid))
        inc_vals = incumbents.market_value_eur.astype(float)
        record = self.capacity.get(buyer, np.nan)
        ls_year = max(y for c, y in self.league_value.index if c == PL)
        row = {
            "prev_share": mins[pid] / (90 * max(games, 1)),
            "log_mv": np.log1p(mv),
            "age": (self.now - dob).days / 365.25 if pd.notna(dob) else np.nan,
            "league_gap": np.log1p(self.league_value.get((self.buyer_comp(buyer), ls_year), np.nan))
            - np.log1p(self.league_value.get((self.comp_of(club), ls_year), np.nan)),
            "rank_at_pos": 1 + int((inc_vals > mv).sum()),
            "value_vs_best": np.log1p(mv) - np.log1p(inc_vals.max()) if len(inc_vals) else 0.0,
            "incumbents": len(incumbents),
            "fee_vs_record": np.log1p(fee) - np.log1p(record) if record == record else 0.0,
        }
        row["age2"] = (row["age"] - 26) ** 2
        x = pd.DataFrame([row])[OPP_FEATURES]
        if x.isna().any(axis=None):
            return None
        return float(self.opp.predict(x)[0])

    # -- club level ------------------------------------------------------------------------------------------
    def squad(self, club_slug: str) -> pd.DataFrame:
        sq = self.squads[self.squads.club_id == club_slug].copy()
        sq["sub_position"] = sq.tm_player_id.map(self.players.sub_position)
        sq["group"] = sq.sub_position.map({s: g for g, subs in GROUPS.items() for s in subs})
        sq["age"] = (self.now - pd.to_datetime(sq.birth_date)).dt.days / 365.25
        return sq

    def _best(self, club_slug: str) -> dict[str, float]:
        """Per position group: market value of the club's best option under 33."""
        sq = self.squad(club_slug)
        return {g: sq.loc[(sq.group == g) & (sq.age < 33), "market_value_eur"].max() for g in NEEDED}

    def needs(self, club_slug: str, peers: int = 6) -> pd.DataFrame:
        """Score each position group: missing depth (vs a two-deep squad), the best option's value against the
        median best option at the same position among the `peers` clubs with the most similar squad strength
        (like with like: goalkeepers vs goalkeepers, a title contender vs title contenders), and ageing.
        Signings the club has announced but who have not joined yet (loaned back, joining next summer) come from
        its official news (`agreed_signings`). Peers come from the club's own league. Higher = bigger need. A
        transparent heuristic, not a fitted model."""
        comp = self.squads.loc[self.squads.club_id == club_slug, "comp"].iloc[0]
        strength = {
            c: self.squad(c).market_value_eur.nlargest(11).median()
            for c in self.squads.loc[self.squads.comp == comp, "club_id"].unique()
        }
        mine = strength[club_slug]
        near = sorted((c for c in strength if c != club_slug), key=lambda c: abs(np.log(strength[c] / mine)))[
            :peers
        ]
        ref = pd.DataFrame([self._best(c) for c in near]).median()
        best = self._best(club_slug)
        sq = self.squad(club_slug)
        rows = []
        for g, want in NEEDED.items():
            at = sq[(sq.group == g) & (sq.age < 33)]
            depth_gap = max(0, want - len(at)) / want
            b = best[g] if best[g] == best[g] else 0.0
            quality_gap = max(0.0, 1 - b / ref[g]) if ref[g] == ref[g] and ref[g] > 0 else 0.0
            ageing = float((sq[(sq.group == g)].age >= 31).mean()) if (sq.group == g).any() else 0.0
            incoming = sum(1 for grp, _, _ in self.incoming.get(club_slug, []) if grp == g)
            ranked = at.sort_values("market_value_eur", ascending=False)
            top = ranked.head(1)
            # the last regular starter: the player a signing would have to be better than to start
            bar = ranked.iloc[STARTERS[g] - 1] if len(ranked) >= STARTERS[g] else None
            rows.append(
                {
                    "group": g,
                    # a signing the club has already announced for this position halves its need
                    "score": (depth_gap + quality_gap + 0.5 * ageing) * 0.5**incoming,
                    "incoming": incoming,
                    "depth": len(at),
                    "depth_gap": depth_gap,
                    "quality_gap": quality_gap,
                    "ageing_share": ageing,
                    # the reasons, in numbers the site can show: depth against the target, the best option's value
                    # against similar squads' best options, and who that best option is
                    "wanted": want,
                    "best_value": b,
                    "peer_best_value": float(ref[g]) if ref[g] == ref[g] else np.nan,
                    "best_name": top.name.iloc[0] if len(top) else None,
                    "bar_name": bar["name"] if bar is not None else None,
                    "bar_value": float(bar.market_value_eur) if bar is not None else np.nan,
                }
            )
        return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)

    def plays(self, ids: pd.Series, primary: pd.Series, group: str) -> pd.Series:
        """Players listed at a position of `group`, or who start there in a quarter of their games."""
        wanted = set(GROUPS[group])
        often = ids.astype(str).map(self.played).map(lambda s: isinstance(s, set) and bool(s & wanted))
        return primary.isin(wanted) | often.astype(bool)

    def shortlist(self, club_slug: str, group: str, tm_club: str) -> pd.DataFrame:
        sq = self.squad(club_slug)
        cap = self.capacity.get(tm_club, np.nan)
        cap = cap if cap == cap and cap > 0 else self.club_value.get(tm_club, 0) * 0.1
        pool = self.players.reset_index()
        pool = pool[
            self.plays(pool.player_id, pool.sub_position, group)
            & (
                pool.current_club_domestic_competition_id.isin(
                    self.comps.loc[self.comps.sub_type == "first_tier", "competition_id"]
                )
            )
            & (pool.current_club_id.astype(str) != tm_club)
            & (pool.last_season >= self.now.year - 1)
        ].copy()
        pool["current_club_id"] = pool.current_club_id.astype(str)
        # transfermarkt-datasets stops in July; the current squads (squad file, other leagues' squads) know who has
        # moved since (e.g. Baleba to Manchester United in August). Use the current club, and leave out anyone who
        # joined his club in the last 12 months: clubs rarely sell a new signing.
        now_sq = self.squads.drop_duplicates("tm_player_id").set_index("tm_player_id")
        in_sq = pool.player_id.isin(now_sq.index)
        pool.loc[in_sq, "current_club_id"] = pool.loc[in_sq, "player_id"].map(now_sq.tm_club_id)
        pool.loc[in_sq, "current_club_name"] = pool.loc[in_sq, "current_club_id"].map(self.club_names())
        pool.loc[in_sq, "current_club_domestic_competition_id"] = pool.loc[in_sq, "player_id"].map(
            now_sq.comp
        )
        joined = pd.to_datetime(pool.player_id.map(now_sq.joined), errors="coerce")
        pool = pool[
            (pool.current_club_id.astype(str) != tm_club)
            & ~(joined > self.now - pd.Timedelta(days=365))
            & ~pool.player_id.astype(str).isin(self.moved)
        ].copy()
        pool["age"] = (self.now - pd.to_datetime(pool.date_of_birth)).dt.days / 365.25
        pool["value"] = pool.player_id.map(self.latest_value)
        recent = (
            self.ap[(self.ap.date >= self.now - pd.Timedelta(days=365))]
            .groupby("player_id")
            .minutes_played.sum()
        )
        pool["minutes_365"] = pool.player_id.map(recent).fillna(0)
        pool = pool[
            pool.age.between(18, 29)
            & (pool.value >= 0.15 * cap)
            & (pool.value <= 1.2 * cap)
            & (pool.minutes_365 >= 1200)
        ]
        pool = pool.nlargest(100, "value")  # the realism/outlook models are per-pair; keep the pool bounded
        if pool.empty:
            return pool
        fees = self.fair_fee(
            pool.player_id.tolist(),
            pool.current_club_domestic_competition_id.tolist(),
            pool.current_club_id.astype(str).tolist(),
            buyer_club=tm_club,
        )
        pool["fair_fee"] = fees.fair_fee.to_numpy()
        incumbents = sq[sq.group == group]
        out = []
        for r in pool.itertuples():
            fee = r.fair_fee if r.fair_fee == r.fair_fee else r.value
            rs = self.realism_score(r.player_id, str(r.current_club_id), tm_club, fee)
            share = self.outlook(r.player_id, tm_club, fee, incumbents)
            if rs is None or share is None:
                continue
            out.append(
                {
                    "tm_player_id": r.player_id,
                    "name": r.name,
                    "club": r.current_club_name,
                    "age": r.age,
                    "value": r.value,
                    "fair_fee": fee,
                    "minutes_share": share,
                    "minutes_365": float(r.minutes_365),
                    **rs,
                }
            )
        df = pd.DataFrame(out)
        if df.empty:
            return df
        # Two lists, as a sporting director reads them. Best fit: the highest quality (market value) among players
        # the playing-time model expects to be regulars, whatever the cost or the odds. Realistic: the moves most
        # likely to happen (realism model), only for players whose club is more likely than average to sell them
        # (sale likelihood at or above the base rate: not an elite club's young regular) and who would be an
        # upgrade (worth at least as much as the incumbent the club would start last at the position).
        # (A single blended score let a 1-in-150 move top the list.)
        df["score"] = (df.value.rank(pct=True) + df.minutes_share.rank(pct=True)) / 2
        regulars = df[df.minutes_share >= REGULAR_SHARE]
        best = (
            regulars.sort_values(["value", "minutes_share"], ascending=False)
            .head(SHORTLIST)
            .assign(kind="best")
        )
        inc = incumbents.market_value_eur.astype(float).sort_values(ascending=False)
        bar = inc.iloc[STARTERS[group] - 1] if len(inc) >= STARTERS[group] else 0.0
        # regulars at Europe's ten richest clubs were sold in 3.6% of summers (realism.md): not realistic targets
        elite = df.tm_player_id.map(self.now_snap.elite_regular).fillna(0) >= 0.5
        # and players move up to richer clubs (realism.md: step-down), so the seller must be the poorer club
        good = df[(df.p_sold >= self.mob_base) & (df.value >= bar) & ~elite & (df.step_up > 0)]
        real = good.sort_values("p_move", ascending=False).head(SHORTLIST).assign(kind="realistic")
        return pd.concat([best, real]).reset_index(drop=True)


def _tm_to_reep(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    from dugout_pipeline.ids import attach_catalogue

    if not attach_catalogue(con):
        return {}
    rows = con.execute(
        "SELECT external_id, reep_id FROM reep.bridges WHERE provider='transfermarkt' AND namespace='spieler'"
    ).fetchall()
    return {str(a): b for a, b in rows}


def recent_moves(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Transfermarkt ids of players Wikipedia's transfer lists show joining a club (or leaving one) since the
    dataset snapshot. Loan returns are not counted: the player is back where the dataset expects him."""
    try:
        rows = con.execute(
            "SELECT DISTINCT tm_player_id FROM wiki_transfers WHERE tm_player_id IS NOT NULL AND kind <> 'end_of_loan'"
        ).fetchall()
    except duckdb.CatalogException:  # warehouse from before the table existed
        return set()
    return {str(r[0]) for r in rows}


def market(con: duckdb.DuckDBPyConnection, now: datetime, clubs: list[str] | None = None) -> list[dict]:
    csv_clubs = set(pd.read_csv(SQUADS, usecols=["club_id"]).club_id)
    mk = Market(now, warehouse_squads(con, skip=csv_clubs))
    mk.incoming = agreed_signings(con, now, set(mk.squads.name))
    mk.moved = recent_moves(con)
    mk.fit()
    reep = _tm_to_reep(con)
    showcase = clubs or sorted(mk.squads.club_id.unique())
    rows: list[dict] = []
    # fair value (if sold to another club in the same league) and sale likelihood for every current squad player
    sq = mk.squads.copy()
    fees = pd.concat(
        [
            mk.fair_fee(
                g.tm_player_id.tolist(), [comp] * len(g), g.tm_club_id.astype(str).tolist(), None, comp
            ).set_index(g.index)
            for comp, g in sq.groupby("comp", sort=False)
        ]
    ).reindex(sq.index)
    sale = mk.sale_likelihood(sq.tm_player_id.tolist())
    for r, f, s in zip(sq.itertuples(), fees.itertuples(), sale.to_numpy(), strict=True):
        pid = (
            r.player_id
            if isinstance(r.player_id, str)
            else reep.get(r.tm_player_id, f"transfermarkt:{r.tm_player_id}")
        )
        if f.fair_fee == f.fair_fee:
            rows += [
                _row("valuation", "player", pid, "fair_fee", round(f.fair_fee, -5), now),
                _row("valuation", "player", pid, "fair_fee_low", round(f.low, -5), now),
                _row("valuation", "player", pid, "fair_fee_high", round(f.high, -5), now),
            ]
        recent = pd.notna(r.joined) and pd.Timestamp(r.joined) > mk.now - pd.Timedelta(days=365)
        if s == s and not recent:  # no data yet on a new signing at his new club: not scored
            rows.append(_row("realism", "player", pid, "sale_likelihood", round(float(s), 3), now))
    for slug in showcase:
        tm_club = mk.squads.loc[mk.squads.club_id == slug, "tm_club_id"].iloc[0]
        needs = mk.needs(slug)
        for i, n in needs.head(2).iterrows():
            rank = i + 1
            rows.append(_row("scout", "club", slug, "need", n.group, now, f"need:{rank}"))
            rows.append(_row("scout", "club", slug, "need_depth", int(n.depth), now, f"need:{rank}"))
            ctx = f"need:{rank}"
            rows.append(_row("scout", "club", slug, "need_wanted", int(n.wanted), now, ctx))
            rows.append(
                _row("scout", "club", slug, "need_ageing_share", round(float(n.ageing_share), 3), now, ctx)
            )
            if n.best_value > 0:
                rows.append(_row("scout", "club", slug, "need_best_value", float(n.best_value), now, ctx))
            if n.peer_best_value == n.peer_best_value:
                rows.append(
                    _row("scout", "club", slug, "need_peer_best_value", float(n.peer_best_value), now, ctx)
                )
            if isinstance(n.best_name, str):
                rows.append(_row("scout", "club", slug, "need_best_player", n.best_name, now, ctx))
            if isinstance(n.bar_name, str) and n.bar_value == n.bar_value:
                rows.append(_row("scout", "club", slug, "need_bar_player", n.bar_name, now, ctx))
                rows.append(_row("scout", "club", slug, "need_bar_value", float(n.bar_value), now, ctx))
            short = mk.shortlist(slug, n.group, str(tm_club))
            for c in short.itertuples():
                k = int((short.kind[: c.Index] == c.kind).sum())  # position within its list, from 0
                pid = reep.get(c.tm_player_id, f"transfermarkt:{c.tm_player_id}")
                ctx = f"{slug}:need:{rank}:{c.kind}:{k + 1}"
                rows += [
                    _row("scout", "player", pid, "candidate_name", c.name, now, ctx),
                    _row("scout", "player", pid, "candidate_club", c.club, now, ctx),
                    _row("scout", "player", pid, "candidate_position", n.group, now, ctx),
                    _row("scout", "player", pid, "age", round(c.age, 1), now, ctx),
                    _row("scout", "player", pid, "market_value", c.value, now, ctx),
                    _row("scout", "player", pid, "fair_fee", round(c.fair_fee, -5), now, ctx),
                    _row(
                        "scout", "player", pid, "expected_minutes_share", round(c.minutes_share, 3), now, ctx
                    ),
                    _row("scout", "player", pid, "minutes_last_year", int(c.minutes_365), now, ctx),
                    _row("scout", "player", pid, "realism", round(c.p_move, 4), now, ctx),
                    _row("scout", "player", pid, "p_sold", round(c.p_sold, 3), now, ctx),
                    _row("scout", "player", pid, "p_this_club", round(c.p_this_club, 4), now, ctx),
                    _row("scout", "player", pid, "shortlist_score", round(c.score, 3), now, ctx),
                ]
                if pd.notna(c.contract_months):
                    rows.append(
                        _row("scout", "player", pid, "contract_months", round(c.contract_months), now, ctx)
                    )
        for j, (group, title, url) in enumerate(mk.incoming.get(slug, []), 1):
            rows.append(_row("scout", "club", slug, "agreed_signing", title, now, f"incoming:{j}"))
            rows.append(_row("scout", "club", slug, "agreed_signing_position", group, now, f"incoming:{j}"))
            rows.append(_row("scout", "club", slug, "agreed_signing_url", url, now, f"incoming:{j}"))
        log.info("market: %s done", slug)
    from dugout_models.lookup import lookup

    return rows + lookup(con, mk, now, reep)
