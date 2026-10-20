"""Transfer market outputs for `publish`: fair value, sale likelihood and a scouting shortlist per club.

Built from transfermarkt-datasets (CC0; valuations and appearances run to June 2026) plus the committed current
squad file `data/reference/transfermarkt_squads.csv` (Premier League and Championship values, positions and
contracts as of the last local refresh; the Championship is a candidate pool: its players can be shortlisted, its
clubs get no lists of their own) and, for leagues without FPL, the football-data.org squads in the warehouse (`warehouse_squads`), valued at
their latest transfermarkt-datasets valuation.

  fair_fee              valuation model (docs/backtests/valuation.md): price if sold to another club in the same
                        league (the model is fitted on buyers in every league; Premier League buyers are a segment)
  sale_likelihood       mobility model (realism.md): P(sold for a fee in a transfer window), features as of today
  needs                 positions where the club is thinnest/weakest (heuristic: depth and value at the position,
                        players counted where they start: positions.group_weights; needs.md)
  shortlist             three lists per need, from first-tier players in every league the dataset covers and
                        the candidate pools' squads (config `candidate_pools`: the Championship).
                        best: highest market value among players the playing-time model (opportunity.md) expects
                        to get at least 40% of the club's minutes, whatever the cost or the odds.
                        realistic: ranked by realism = P(move to this club in a window) = P(sold) x P(this buyer |
                        sold) (realism.md; the second factor counts the past year's press reports linking him
                        with this club, rumour_links), among players whose club is poorer than the buyer, is more
                        likely than average to sell them and is not a top-ten-in-Europe club starting them, and who
                        would be an upgrade on the club's last regular starter at the position.
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
from dugout_models.positions import GROUP_STARTS, group_weights, played, positions_played
from dugout_models.publish import _row
from dugout_models.style import StyleModel, describe, fit_vs, lacks, standouts, top_share

log = logging.getLogger(__name__)

SQUADS = REPO_ROOT / "data" / "reference" / "transfermarkt_squads.csv"
MINUTES = REPO_ROOT / "data" / "reference" / "transfermarkt_minutes.csv"  # candidate pools' league minutes
PL = "GB1"
# Transfermarkt sub-position -> the dataset's broad position (valuation features use it)
BROAD = {
    "Goalkeeper": "Goalkeeper",
    "Centre-Back": "Defender",
    "Left-Back": "Defender",
    "Right-Back": "Defender",
    "Defensive Midfield": "Midfield",
    "Central Midfield": "Midfield",
    "Attacking Midfield": "Midfield",
    "Left Midfield": "Midfield",
    "Right Midfield": "Midfield",
    "Left Winger": "Attack",
    "Right Winger": "Attack",
    "Centre-Forward": "Attack",
    "Second Striker": "Attack",
}
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
GROUP_OF = {s: g for g, subs in GROUPS.items() for s in subs}
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
PRESS_LIFT_SHOWN = (
    1.1  # press reports "moved the odds" when they make the move at least this many times likelier
)


def pool_config() -> dict[str, dict]:
    """Candidate pools (config `candidate_pools`) by Transfermarkt competition id."""
    from dugout_pipeline.clubs import load_config

    return {p["transfermarkt"]: p for p in (load_config().get("candidate_pools") or {}).values()}


def pool_comps() -> set[str]:
    """Competitions of the candidate pools: shortlist candidates, no lists of their own."""
    return set(pool_config())


def pool_appearances(minutes: pd.DataFrame, pools: dict[str, dict]) -> pd.DataFrame:
    """Appearance rows for a candidate pool's last season, which transfermarkt-datasets lacks: each player's league
    appearances (Transfermarkt performance pages) spread evenly over the season's matchdays (`stats_season`), with
    his minutes, goals and assists shared equally between them. Per-game figures are therefore averages; totals,
    windows over the season and shares of a club's games come out as on the page."""
    out = []
    for comp, g in minutes.groupby("comp"):
        st = (pools.get(comp) or {}).get("stats_season")
        if not st:
            continue
        n = int(st["matchdays"])
        days = pd.date_range(st["first"], st["last"], periods=n).strftime("%Y-%m-%d")
        for r in g.itertuples():
            apps = min(int(r.apps), n)
            if apps <= 0:
                continue
            for i in np.unique(np.linspace(0, n - 1, apps).round().astype(int)):
                out.append(
                    {
                        "appearance_id": f"{comp}-{r.tm_club_id}-{i}-{r.tm_player_id}",
                        "game_id": -(
                            int(r.tm_club_id) * 1000 + int(i)
                        ),  # no real game id: negative, per club
                        "player_id": str(r.tm_player_id),
                        "player_club_id": str(r.tm_club_id),
                        "date": days[i],
                        "player_name": r.name,
                        "competition_id": comp,
                        "yellow_cards": 0,
                        "red_cards": 0,
                        "goals": r.goals / apps,
                        "assists": r.assists / apps,
                        "minutes_played": r.minutes / apps,
                    }
                )
    return pd.DataFrame(out)


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
        # the committed squad file (Premier League and candidate pools, refreshed locally) plus squads of other
        # leagues (warehouse); `comp` is each club's Transfermarkt competition: buyer-league features use it
        self.squads = pd.read_csv(SQUADS, dtype={"tm_player_id": str, "tm_club_id": str})
        self.squads["comp"] = self.squads.comp.fillna(PL) if "comp" in self.squads else PL
        self.pools = pool_comps()
        self.incoming: dict[str, list[tuple]] = {}  # filled from official announcements by market()
        self.moved: set[str] = set()  # moved since the dataset snapshot (Wikipedia lists), filled by market()
        self.links = realism.load_links(None)  # rumour links (archive + recent), filled by market()
        lineups = played()
        self.played = positions_played(p=lineups)  # positions players actually start at (positions.py)
        self.players = self.t["players"].copy()
        self.players["player_id"] = self.players.player_id.astype(str)
        self.players = self.players.set_index("player_id")
        self.use_positions(lineups)
        if extra_squads is not None and len(extra_squads):
            self.squads = pd.concat([self.squads, self._fill(extra_squads)], ignore_index=True)
        self._with_pool_players()
        self._with_current_values()
        self.clubs = self._live_clubs()

    def _with_pool_players(self) -> None:
        """Candidate-pool players (e.g. the Championship) as players-table rows: those the dataset does not know are
        added from the squad file (name, birth date, position), and every pool player gets the squad file's club,
        league and contract, which are newer than the dataset's July snapshot."""
        sq = (
            self.squads[self.squads.comp.isin(self.pools)]
            .drop_duplicates("tm_player_id")
            .set_index("tm_player_id")
        )
        if sq.empty:
            return
        sub = sq.position if "position" in sq else pd.Series(np.nan, index=sq.index)

        def stamp(
            d: pd.Series,
        ) -> pd.Series:  # the dataset's date format ("2002-08-09 00:00:00"), never mixed
            return pd.to_datetime(d, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

        new = pd.DataFrame(
            {
                "name": sq.name,
                "date_of_birth": stamp(sq.birth_date),
                "sub_position": sub,
                "position": sub.map(BROAD),
                "last_season": self.now.year,
            },
            index=sq.index[~sq.index.isin(self.players.index)],
        )
        # object columns first: rows without a club would turn integer club ids into floats ("1234.0")
        for col in ("current_club_id", "current_club_domestic_competition_id", "contract_expiration_date"):
            self.players[col] = self.players[col].astype(object)
        self.players = pd.concat([self.players, new]).rename_axis("player_id")
        self.players["last_season"] = pd.to_numeric(self.players.last_season, errors="coerce").astype(float)
        ids = sq.index
        self.players.loc[ids, "current_club_id"] = sq.tm_club_id
        self.players.loc[ids, "current_club_domestic_competition_id"] = sq.comp
        self.players.loc[ids, "contract_expiration_date"] = stamp(sq.contract_expires)
        self.players.loc[ids, "sub_position"] = self.players.loc[ids, "sub_position"].fillna(sub)
        self.players.loc[ids, "last_season"] = (
            self.players.loc[ids, "last_season"].fillna(0).clip(lower=self.now.year)
        )
        # the dataset tables the potential and realism snapshots read (rows of players with no history add nothing
        # to the training frames: those start from valuations dated before today)
        t_new = new.reset_index(names="player_id")
        self.t["players"] = pd.concat(
            [self.t["players"].assign(player_id=lambda d: d.player_id.astype(str)), t_new], ignore_index=True
        )

    def _live_clubs(self) -> pd.DataFrame:
        """The dataset's clubs with today's league for every club in the current squads, and the clubs it lacks
        (promoted clubs such as Coventry and Hull, most of the Championship). The dataset keeps a relegated club in
        its old league; live scoring (sale odds, buyers, league gaps) needs the league it plays in now. Training
        frames keep the dataset's clubs."""
        clubs = self.t["clubs"].copy()
        clubs["club_id"] = clubs.club_id.astype(str)
        now = self.squads.drop_duplicates("tm_club_id").set_index("tm_club_id")
        names = self.club_names()
        known = clubs.club_id.isin(now.index)
        clubs.loc[known, "domestic_competition_id"] = clubs.loc[known, "club_id"].map(now.comp)
        missing = now.index[~now.index.isin(clubs.club_id)]
        extra = pd.DataFrame(
            {
                "club_id": missing,
                "name": [names.get(c) for c in missing],
                "domestic_competition_id": now.comp[missing],
            }
        )
        return pd.concat([clubs, extra], ignore_index=True)

    def _with_pool_minutes(self) -> None:
        """The candidate pools' last-season league games (`pool_appearances`) join the appearances that live scoring
        reads: minutes in the past year, share of the club's games, goals and assists per 90, last season's minutes
        for the playing-time model. The realism, opportunity and potential training frames are built from other
        tables or from years before these games."""
        if not MINUTES.exists():
            return
        m = pd.read_csv(MINUTES, dtype={"tm_player_id": str, "tm_club_id": str})
        extra = pool_appearances(m[m.comp.isin(self.pools)], pool_config())
        if len(extra):
            extra["player_current_club_id"] = extra.player_id.map(self.players.current_club_id)
            self.t["appearances"] = pd.concat([self.t["appearances"], extra], ignore_index=True)

    def buying_clubs(self) -> list[str]:
        """Club slugs that get needs and shortlists: every squad except the candidate pools'."""
        return sorted(self.squads.loc[~self.squads.comp.isin(self.pools), "club_id"].unique())

    def use_positions(self, lineups: pd.DataFrame, min_starts: int = GROUP_STARTS) -> None:
        """Where each player counts in a squad's depth: the groups he starts in (positions.group_weights), his
        listed position below `min_starts` recorded starts. (A huge `min_starts` gives the listed positions only:
        the grouping before 2026-10-18, kept for the needs backtest.)"""
        self.weights = group_weights(
            lineups, self.players.sub_position, GROUP_OF, min_starts=min_starts
        ).rename(columns={"player_id": "tm_player_id"})
        main = self.weights.sort_values(["weight", "group"]).drop_duplicates("tm_player_id", keep="last")
        self.main_group = main.set_index("tm_player_id").group

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
        if "club_name" in self.squads:  # warehouse squads and candidate pools (the squad file's club names)
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
        self.val = valuation.ValuationModel().fit(df[df.date <= self.now]).calibrate(df, self.now)
        self._with_pool_minutes()  # after the valuation frame: training data stays the dataset's
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
            tables="raw",  # frames built from Market's string-id tables (broken, maybe cached) are not reused
        )
        snap = snap.dropna(subset=realism.MOBILITY)
        pairs = self.frame(
            "destination",
            lambda: realism.destination_pairs(tables(), range(2015, 2026), people=timeline()),
            years=[2015, 2025],
            features=realism.DESTINATION,
            clubs="dated",
            tables="raw",
        )
        if len(pairs.dropna(subset=realism.DESTINATION)) == 0 or len(snap) == 0:
            raise ValueError("realism training frames are empty: check club id types (market.py tables())")
        pairs = pairs.dropna(subset=realism.DESTINATION)
        # press reports before each 1 June (rumour_links): added after the cache, so the cached frames do not
        # depend on the rumour archive; with no archive yet the columns are all zero and carry no weight
        snap, pairs = realism.with_news(snap, pairs, self.links)
        self.mob = realism.mobility_model().fit(snap[realism.MOBILITY_SHIPPED], snap.moved)
        self.mob_base = float(snap.moved.mean())
        self.dest = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(
            pairs[realism.DESTINATION_SHIPPED], pairs.label
        )
        self.dest_base = float(pairs.label.mean())
        self.opp = OpportunityModel().fit(self.frame("opportunity", opportunity_frame))
        now_snap = realism.snapshots(self.t, [self.now.year], as_of=self.now)
        now_snap = now_snap.join(
            realism.player_news(self.links, now_snap.player_id, pd.Series(self.now, index=now_snap.index))
        )
        self.now_snap = now_snap.set_index("player_id")
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
        df["to_comp"] = to  # the buyer league's offset (valuation.league_offsets)
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

    def sale_likelihood(self, ids: list[str], press: bool = True) -> pd.Series:
        """P(sold for a fee in a window). press=False: as if nothing had been written about him (for the "why")."""
        snap = self.now_snap.reindex(ids)
        ok = snap.dropna(subset=realism.MOBILITY_SHIPPED)
        if not press:
            ok = ok.assign(**dict.fromkeys(realism.NEWS_MOBILITY, 0.0))
        p = pd.Series(np.nan, index=ids)
        if len(ok):
            p.loc[ok.index] = self.mob.predict_proba(ok[realism.MOBILITY_SHIPPED])[:, 1]
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
        is a 1-in-6 comparison, not a probability over the market). The contract prior shifts the sale odds.
        Press reports (rumour_links, the past year) are fitted features of whichever model they helped on validation
        (realism.NEWS_*: the destination model; realism.md); `press_lift` is how many times likelier they make this
        move than the same estimate with nothing written about him."""
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
        )
        mine = self.links[self.links.tm_player_id == str(pid)]
        idx = cand.index
        X = X.join(
            realism.pair_news(
                mine,
                pd.Series(str(pid), index=idx),
                pd.Series(idx, index=idx),
                pd.Series(self.now, index=idx),
            )
        )
        expiry = pd.to_datetime(self.players.contract_expiration_date.get(pid))
        months = (expiry - self.now).days / 30.44 if pd.notna(expiry) else None

        def move(x: pd.DataFrame, p_mob: float) -> tuple[float, float, float]:
            p = self.dest.predict_proba(x[realism.DESTINATION_SHIPPED])[:, 1]
            odds = pd.Series(p / (1 - p), index=cand.index)
            share = float(odds[buyer] / odds.sum())
            sold_odds = p_mob / (1 - p_mob) * np.exp(realism.contract_points(months))
            p_sold = sold_odds / (1 + sold_odds)
            return p_sold * share, p_sold, share

        p_move, p_sold, share = move(X, p_mob)
        quiet, _, _ = move(
            X.assign(**dict.fromkeys(realism.NEWS_DESTINATION, 0.0)),
            self.sale_likelihood([pid], press=False).iloc[0],
        )
        reports = round(float(np.expm1(X.at[buyer, "news_club_365"])))
        return {
            "p_move": float(p_move),
            "p_sold": float(p_sold),
            "p_this_club": share,
            "step_up": float(bt.loc[buyer].cv - s.cv),  # > 0: the buyer is the richer club
            "contract_months": None if months is None else float(months),
            "press_reports": reports,  # reports linking him with this buyer in the past year
            "press_lift": float(p_move / quiet) if quiet > 0 else np.nan,
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
        sq["group"] = sq.tm_player_id.map(
            self.main_group
        )  # the group he starts in most (positions.group_weights)
        sq["age"] = (self.now - pd.to_datetime(sq.birth_date)).dt.days / 365.25
        return sq

    def depth(self, club_slug: str) -> pd.DataFrame:
        """The squad by where players start: one row per (player, position group) he starts in for 25%+ of his
        starts, `weight` his share there (each player adds up to one). Players with too few recorded starts (new
        signings, youngsters) count at their listed position (positions.group_weights)."""
        sq = self.squad(club_slug).drop(columns="group")
        return sq.merge(self.weights[["tm_player_id", "group", "weight", "source"]], on="tm_player_id")

    def at(self, club_slug: str, group: str) -> pd.DataFrame:
        """The club's options for `group`: everyone who starts there for 25%+ of his starts."""
        d = self.depth(club_slug)
        return d[d.group == group]

    def _best(self, club_slug: str) -> dict[str, float]:
        """Per position group: market value of the club's best option under 33."""
        d = self.depth(club_slug)
        return {g: d.loc[(d.group == g) & (d.age < 33), "market_value_eur"].max() for g in NEEDED}

    def needs(self, club_slug: str, peers: int = 6) -> pd.DataFrame:
        """Score each position group: missing depth (vs a two-deep squad), the best option's value against the
        median best option at the same position among the `peers` clubs with the most similar squad strength
        (like with like: goalkeepers vs goalkeepers, a title contender vs title contenders), and ageing.
        Depth counts players where they start (`depth`): a centre-back with 40% of his starts at right-back is
        0.4 of a full-back; peers are grouped the same way. Signings the club has announced but who have not
        joined yet (loaned back, joining next summer) come from its official news (`agreed_signings`). Peers come
        from the club's own league. Higher = bigger need. A transparent heuristic, not a fitted model."""
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
        dp = self.depth(club_slug)
        rows = []
        for g, want in NEEDED.items():
            options = dp[dp.group == g]
            at = options[options.age < 33]
            depth = float(at.weight.sum())
            depth_gap = max(0.0, want - depth) / want
            b = best[g] if best[g] == best[g] else 0.0
            quality_gap = max(0.0, 1 - b / ref[g]) if ref[g] == ref[g] and ref[g] > 0 else 0.0
            # share of the group's starts-weighted depth held by veterans
            total = options.weight.sum()
            ageing = float(options.weight[options.age >= 31].sum() / total) if total > 0 else 0.0
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
                    "depth": depth,
                    "players": len(at),  # how many players that depth is spread over
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
                    "bar_tm": str(bar.tm_player_id) if bar is not None else None,
                    "bar_ga_p90": (
                        (lambda pr: pr.get("goals_p90", np.nan) + pr.get("assists_p90", np.nan))(
                            self.profile(bar.tm_player_id, g)
                        )
                        if bar is not None
                        else np.nan
                    ),
                }
            )
        return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)

    PROFILE_MIN_MINUTES = 900  # league minutes before per-90 figures and rankings mean anything

    def _league_window(self) -> pd.DataFrame:
        """League appearances (each player's domestic league) over the last year on record, cached."""
        if not hasattr(self, "_league_ap"):
            ap = self.ap[(self.ap.date < self.now) & (self.ap.date >= self.now - pd.Timedelta(days=365))]
            dom = self.comps.loc[self.comps.sub_type.isin(["first_tier", "second_tier"]), "competition_id"]
            self._league_ap = ap[ap.competition_id.isin(set(dom) | self.pools)]
        return self._league_ap

    def _peers(self, comp: str, group: str) -> np.ndarray:
        """Goal involvements per 90 of everyone in `comp` who plays `group` with enough league minutes."""
        key = (comp, group)
        cache = self.__dict__.setdefault("_peer_cache", {})
        if key not in cache:
            ap = self._league_window()
            g = (
                ap[ap.competition_id == comp]
                .groupby("player_id")[["minutes_played", "goals", "assists"]]
                .sum()
            )
            g = g[g.minutes_played >= self.PROFILE_MIN_MINUTES]
            ids = pd.Series(g.index.astype(str), index=g.index)
            sub = ids.map(self.players.sub_position)
            g = g[self.plays(ids, sub, group).to_numpy()]
            cache[key] = ((g.goals + g.assists) / g.minutes_played * 90).to_numpy()
        return cache[key]

    def profile(self, pid: str, group: str) -> dict:
        """How a player plays and produces, from free data: league goals and assists per 90 over the last year on
        record, his rank for goal involvement among players at the position in his league, the positions he
        starts at, his stronger foot, height and caps. (No passing or defending data is free for most leagues.)"""
        pid = str(pid)
        ap = self._league_window()
        mine = ap[ap.player_id.astype(str) == pid]
        out: dict = {"league_minutes": float(mine.minutes_played.sum())}
        if out["league_minutes"] >= self.PROFILE_MIN_MINUTES and len(mine):
            m = out["league_minutes"]
            out["goals_p90"] = float(mine.goals.sum() / m * 90)
            out["assists_p90"] = float(mine.assists.sum() / m * 90)
            comp = mine.competition_id.mode().iloc[0]
            peers = self._peers(comp, group)
            if len(peers) >= 10:
                out["ga_pctile"] = float((peers < out["goals_p90"] + out["assists_p90"]).mean())
                slug = str(self.comps.set_index("competition_id").name.get(comp) or "")
                named = {"laliga": "La Liga"}.get(slug, slug.replace("-", " ").title())
                out["ga_league"] = named or (pool_config().get(comp) or {}).get("name")
        pl = self.players
        if pid in pl.index:
            foot, height, caps = pl.foot.get(pid), pl.height_in_cm.get(pid), pl.international_caps.get(pid)
            out["foot"] = foot if isinstance(foot, str) and foot in ("left", "right", "both") else None
            out["height_cm"] = float(height) if height == height and height else None
            out["caps"] = float(caps) if caps == caps and caps is not None else None
        played = self.played.get(pid)
        out["positions"] = ", ".join(sorted(p.lower() for p in played)) if isinstance(played, set) else None
        return out

    def plays(self, ids: pd.Series, primary: pd.Series, group: str) -> pd.Series:
        """Players listed at a position of `group`, or who start there in a quarter of their games."""
        wanted = set(GROUPS[group])
        often = ids.astype(str).map(self.played).map(lambda s: isinstance(s, set) and bool(s & wanted))
        return primary.isin(wanted) | often.astype(bool)

    def shortlist(self, club_slug: str, group: str, tm_club: str) -> pd.DataFrame:
        cap = self.capacity.get(tm_club, np.nan)
        cap = cap if cap == cap and cap > 0 else self.club_value.get(tm_club, 0) * 0.1
        pool = self.players.reset_index()
        leagues = set(self.comps.loc[self.comps.sub_type == "first_tier", "competition_id"]) | self.pools
        pool = pool[
            self.plays(pool.player_id, pool.sub_position, group)
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
            pool.current_club_domestic_competition_id.isin(leagues)  # a first tier or a candidate pool, today
            & (pool.current_club_id.astype(str) != tm_club)
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
        pool["fee_low"], pool["fee_high"] = fees.low.to_numpy(), fees.high.to_numpy()
        incumbents = self.at(club_slug, group)  # everyone who starts there, as in needs()
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
                    "fee_low": r.fee_low if r.fair_fee == r.fair_fee else np.nan,
                    "fee_high": r.fee_high if r.fair_fee == r.fair_fee else np.nan,
                    "minutes_share": share,
                    "minutes_365": float(r.minutes_365),
                    **self.profile(r.player_id, group),
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


PROFILE_METRICS = {  # profile key -> (published metric, rounding); text values are published as they are
    "goals_p90": ("goals_p90", 2),
    "assists_p90": ("assists_p90", 2),
    "ga_pctile": ("ga_pctile", 2),
    "ga_league": ("ga_league", None),
    "foot": ("foot", None),
    "height_cm": ("height_cm", 0),
    "caps": ("caps", 0),
    "positions": ("positions_played", None),
}


def fee_range_rows(model: str, pid: str, low: float, high: float, now: datetime, ctx: str = "") -> list[dict]:
    """The fair fee's likely range (valuation.md: LEVEL of fees land inside) in €0.1m steps, rounded outwards; no
    rows when it is unknown or the low end would round to nothing."""
    if not (low == low and high == high):
        return []
    lo, hi = np.floor(low / 1e5) * 1e5, np.ceil(high / 1e5) * 1e5
    if lo <= 0:
        return []
    return [
        _row(model, "player", pid, "fair_fee_low", float(lo), now, ctx),
        _row(model, "player", pid, "fair_fee_high", float(hi), now, ctx),
    ]


def profile_rows(c: dict, pid: str, now: datetime, ctx: str) -> list[dict]:
    """model_outputs rows for a candidate's profile (Market.profile), skipping what is unknown."""
    rows = []
    for key, (metric, nd) in PROFILE_METRICS.items():
        v = c.get(key)
        if v is None or (isinstance(v, float) and v != v):
            continue
        if isinstance(v, float) and nd is not None:
            v = round(v, nd) if nd else round(v)
        rows.append(_row("scout", "player", pid, metric, v, now, ctx))
    return rows


def starter_rows(club: str, st: dict | None, now: datetime, ctx: str) -> list[dict]:
    """The last regular starter at a need, in style terms (dugout_models.style): his role, the season and pool he
    is measured in, his strongest features (top share) and his weakest (percentile: what the side lacks there)."""
    if not st:
        return []
    rows = [
        _row("scout", "club", club, "need_bar_style_season", st["season"], now, ctx),
        _row("scout", "club", club, "need_bar_style_pool", st["pool"], now, ctx),
    ]
    if st["role"]:
        rows.append(_row("scout", "club", club, "need_bar_style_role", st["role"], now, ctx))
    for f, v in standouts(st["pct"], 2):
        rows.append(_row("scout", "club", club, f"need_bar_top:{f}", top_share(v), now, ctx))
    for f, v in lacks(st):
        rows.append(_row("scout", "club", club, f"need_bar_low:{f}", round(v, 2), now, ctx))
    return rows


def style_rows(c: dict | None, starter: dict | None, pid: str, now: datetime, ctx: str) -> list[dict]:
    """A candidate's style for the need (dugout_models.style.describe): role, season, pool, his three strongest
    features as top shares ("top 10%"), the feature he adds most over the starter he would replace (both
    percentiles) and up to two similar players."""
    if not c:
        return []
    rows = [
        _row("scout", "player", pid, "style_season", c["season"], now, ctx),
        _row("scout", "player", pid, "style_pool", c["pool"], now, ctx),
    ]
    if c["role"]:
        rows.append(_row("scout", "player", pid, "style_role", c["role"], now, ctx))
    for f, v in standouts(c["pct"], 3):
        rows.append(_row("scout", "player", pid, f"style_top:{f}", top_share(v), now, ctx))
    adds = fit_vs(c, starter)
    if adds:
        f, mine, theirs = adds
        rows.append(_row("scout", "player", pid, f"style_adds:{f}", round(mine, 2), now, ctx))
        rows.append(_row("scout", "player", pid, f"style_adds_bar:{f}", round(theirs, 2), now, ctx))
    if c["similar"]:
        rows.append(_row("scout", "player", pid, "style_similar", " and ".join(c["similar"]), now, ctx))
    return rows


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


RUMOUR_DAYS = 120  # rumours scored with their brief (rumours.publish recent_days)


def _tm_id(x) -> str | None:
    """A Transfermarkt id as a plain string ("1234", never "1234.0"); None when missing."""
    if x is None or (isinstance(x, float) and np.isnan(x)) or x == "" or x == "nan":
        return None
    try:
        return str(int(float(x)))
    except (TypeError, ValueError):
        return None


def fee_sales(mk: Market, con: duckdb.DuckDBPyConnection, seller: str, since: pd.Timestamp) -> int:
    """Players the club sold for a fee since `since`: transfermarkt-datasets to its snapshot, then Wikipedia's
    summer-2026 lists (a list without a date: its summer window)."""
    tr = mk.t["transfers"]
    fee = pd.to_numeric(tr.transfer_fee, errors="coerce")
    when = pd.to_datetime(tr.transfer_date)
    sold = set(
        tr.player_id[
            (fee > 0) & (when >= since) & (when <= mk.now) & (tr.from_club_id.map(_tm_id) == seller)
        ].map(_tm_id)
    )
    try:
        wiki = con.execute(
            """SELECT tm_player_id FROM wiki_transfers WHERE tm_from_club = ? AND fee_eur > 0
               AND coalesce(date, DATE '2026-07-01') >= CAST(? AS DATE) AND tm_player_id IS NOT NULL""",
            [seller, since.date().isoformat()],
        ).fetchall()
    except duckdb.CatalogException:
        wiki = []
    return len(sold | {_tm_id(w[0]) for w in wiki})


def rumour_reality(con: duckdb.DuckDBPyConnection, mk: Market, now: datetime) -> list[dict]:
    """The reality check behind each recent rumour (rumour_links, written by rumours.publish just before): this
    move's chance in a window (sale likelihood x this buyer, as for the realistic list), his contract and how
    often his club sells. Rows: model 'scout', entity 'rumour' (the rumour brief's id, context = the buyer's
    slug) and entity 'seller' (Transfermarkt club id: fee sales since the summer three years back)."""
    try:
        links = con.execute(
            """SELECT DISTINCT rumour_id, player_name, tm_player_id, tm_club_id, from_tm_club FROM rumour_links
               WHERE published_at >= ?""",
            [pd.Timestamp(now) - pd.Timedelta(days=RUMOUR_DAYS)],
        ).df()
    except duckdb.CatalogException:
        return []
    from dugout_models.rumours import fold

    buyers = mk.squads[mk.squads.club_id.isin(mk.buying_clubs())].drop_duplicates("club_id")
    slug_of = dict(zip(buyers.tm_club_id.astype(str), buyers.club_id, strict=True))
    now_sq = mk.squads.drop_duplicates("tm_player_id").set_index("tm_player_id")
    names = mk.club_names()
    year = mk.now.year - 3 if mk.now.month >= 7 else mk.now.year - 4
    since = pd.Timestamp(year, 7, 1)
    rows: list[dict] = []
    sellers: set[str] = set()
    for r in links.itertuples():
        buyer = _tm_id(r.tm_club_id)
        if buyer not in slug_of:
            continue
        pid = _tm_id(r.tm_player_id)
        # his club today: the squads know who moved since the rumour (and since the dataset's snapshot)
        seller = _tm_id(now_sq.tm_club_id.get(pid)) or _tm_id(r.from_tm_club)
        if pid is None or seller is None or seller == buyer:
            continue
        comp = mk.comp_of(seller) or mk.buyer_comp(seller)
        fee = mk.fair_fee([pid], [comp], [seller], buyer_club=buyer).fair_fee.iloc[0]
        fee = fee if fee == fee else mk.latest_value.get(pid, np.nan)
        if pd.isna(fee):
            continue
        rs = mk.realism_score(pid, seller, buyer, float(fee))
        if rs is None:
            continue
        eid, ctx = f"{r.rumour_id}:{fold(r.player_name)}", slug_of[buyer]
        rows += [
            _row("scout", "rumour", eid, "realism", round(rs["p_move"], 4), now, ctx),
            _row("scout", "rumour", eid, "p_sold", round(rs["p_sold"], 3), now, ctx),
            _row("scout", "rumour", eid, "p_this_club", round(rs["p_this_club"], 4), now, ctx),
            _row("scout", "rumour", eid, "seller", seller, now, ctx),
        ]
        if rs["contract_months"] is not None and rs["contract_months"] > 0:
            rows.append(
                _row("scout", "rumour", eid, "contract_months", round(rs["contract_months"]), now, ctx)
            )
        if seller not in sellers:
            sellers.add(seller)
            rows += [
                _row("scout", "seller", seller, "name", names.get(seller), now),
                _row("scout", "seller", seller, "fee_sales", fee_sales(mk, con, seller, since), now),
                _row("scout", "seller", seller, "fee_sales_since", since.date().isoformat(), now),
            ]
    return [r for r in rows if r["value_num"] is not None or r["value_text"] is not None]


def market(con: duckdb.DuckDBPyConnection, now: datetime, clubs: list[str] | None = None) -> list[dict]:
    csv_clubs = set(pd.read_csv(SQUADS, usecols=["club_id"]).club_id)
    mk = Market(now, warehouse_squads(con, skip=csv_clubs))
    mk.incoming = agreed_signings(con, now, set(mk.squads.name))
    mk.moved = recent_moves(con)
    mk.links = realism.load_links(con)
    mk.fit()
    mk.style = StyleModel.from_warehouse(con, mk.main_group)
    reep = _tm_to_reep(con)
    showcase = clubs or mk.buying_clubs()
    rows: list[dict] = []
    # fair value (if sold to another club in the same league) and sale likelihood for every current squad player
    # (candidate pools have no pages: their players' figures are published only where they are shortlisted)
    sq = mk.squads[~mk.squads.comp.isin(mk.pools)].copy()
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
            rows.append(_row("valuation", "player", pid, "fair_fee", round(f.fair_fee, -5), now))
            rows += fee_range_rows("valuation", pid, f.low, f.high, now)
        recent = pd.notna(r.joined) and pd.Timestamp(r.joined) > mk.now - pd.Timedelta(days=365)
        if s == s and not recent:  # no data yet on a new signing at his new club: not scored
            rows.append(_row("realism", "player", pid, "sale_likelihood", round(float(s), 3), now))
    for slug in showcase:
        tm_club = mk.squads.loc[mk.squads.club_id == slug, "tm_club_id"].iloc[0]
        needs = mk.needs(slug)
        for i, n in needs.head(2).iterrows():
            rank = i + 1
            rows.append(_row("scout", "club", slug, "need", n.group, now, f"need:{rank}"))
            # starts-weighted depth: one decimal (a centre-back who starts a third of games at full-back is 0.3 there)
            rows.append(
                _row("scout", "club", slug, "need_depth", round(float(n.depth), 1), now, f"need:{rank}")
            )
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
            if n.bar_ga_p90 == n.bar_ga_p90:
                rows.append(
                    _row("scout", "club", slug, "need_bar_ga_p90", round(float(n.bar_ga_p90), 2), now, ctx)
                )
            # how the last regular starter there plays: what a signing would replace, and what the side lacks
            starter = describe(mk.style, n.bar_tm, n.group) if isinstance(n.bar_tm, str) else None
            rows += starter_rows(slug, starter, now, ctx)
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
                    *fee_range_rows("scout", pid, c.fee_low, c.fee_high, now, ctx),
                    _row(
                        "scout", "player", pid, "expected_minutes_share", round(c.minutes_share, 3), now, ctx
                    ),
                    _row("scout", "player", pid, "minutes_last_year", int(c.minutes_365), now, ctx),
                    *profile_rows(c._asdict(), pid, now, ctx),
                    *style_rows(describe(mk.style, c.tm_player_id, n.group), starter, pid, now, ctx),
                    _row("scout", "player", pid, "realism", round(c.p_move, 4), now, ctx),
                    _row("scout", "player", pid, "p_sold", round(c.p_sold, 3), now, ctx),
                    _row("scout", "player", pid, "p_this_club", round(c.p_this_club, 4), now, ctx),
                    _row("scout", "player", pid, "shortlist_score", round(c.score, 3), now, ctx),
                ]
                if pd.notna(c.contract_months):
                    rows.append(
                        _row("scout", "player", pid, "contract_months", round(c.contract_months), now, ctx)
                    )
                if (
                    c.press_reports > 0
                ):  # the press has linked him with this club: say how far it moved the odds
                    rows.append(_row("scout", "player", pid, "press_reports", int(c.press_reports), now, ctx))
                    if c.press_lift >= PRESS_LIFT_SHOWN:  # NaN fails too
                        rows.append(
                            _row("scout", "player", pid, "press_lift", round(c.press_lift, 1), now, ctx)
                        )
        for j, (group, title, url) in enumerate(mk.incoming.get(slug, []), 1):
            rows.append(_row("scout", "club", slug, "agreed_signing", title, now, f"incoming:{j}"))
            rows.append(_row("scout", "club", slug, "agreed_signing_position", group, now, f"incoming:{j}"))
            rows.append(_row("scout", "club", slug, "agreed_signing_url", url, now, f"incoming:{j}"))
        log.info("market: %s done", slug)
    rows += rumour_reality(con, mk, now)
    from dugout_models.lookup import lookup

    return rows + lookup(con, mk, now, reep)
