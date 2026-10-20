"""API-Football player season statistics (free plan: seasons 2022-2024), for the playing-style model.

`players?team=&league=&season=` gives a club's players' league totals for a season: key passes, dribbles, tackles,
interceptions, duels, shots, fouls, cards. 20 players a page, and the free plan stops at page 3 (checked
2026-10-19: "Free plans are limited to a maximum value of 3 for the Page parameter"), so queries go club by club
(a whole league would stop after 60 players); a club's league squad fits in three pages. ~3 requests a club, ~350
for the six leagues' 2024-25 clubs, so the backfill walks a fixed queue a little each night:

1. the six configured leagues' clubs, season 2024 (2024-25, the newest the free plan has): one `teams` call per
   league for the clubs, then their pages;
2. the 2024-25 clubs of current shortlist candidates who played in another league (their club from
   transfermarkt-datasets appearances, its API-Football id from Reep, the league from LEAGUES);
3. the six leagues again for 2023 (season-to-season stability of the roles, players short of minutes in 2024).

Responses are cached for a year (seasons are closed), so a run re-reads what it already has for free and continues
where the last one stopped. Shares API-Football's cache and daily budget (`client_name`); never more than DAILY_CAP
requests a UTC day, and stops while STATS_RESERVE of the adapter's requests are left (its limit already holds
BUDGET_RESERVE back): 25 of the account's 100 stay for the nightly's lineups and the 4-hourly injuries runs. It
re-reads the account's count every few calls (other machines share it). The account was suspended once: never
exceed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb
import pandas as pd

from dugout_pipeline.adapters.api_football import ApiFootballHistoryAdapter, season_squads
from dugout_pipeline.adapters.base import DAY
from dugout_pipeline.adapters.transfermarkt import TM_DIR
from dugout_pipeline.clubs import league_config, leagues
from dugout_pipeline.db import upsert
from dugout_pipeline.http import BudgetExceeded, DailyBudget
from dugout_pipeline.ids import attach_catalogue
from dugout_pipeline.matching import best_player, squad_player

log = logging.getLogger(__name__)

DAILY_CAP = 70  # this backfill's own requests per UTC day
STATS_RESERVE = 15  # + BUDGET_RESERVE (10) = 25 of the account's 100 left for everything else
SEASONS = (2024, 2023)  # newest first; the free plan also has 2022 (not needed yet)
MAX_PAGE = 3  # the free plan's limit
TTL = 365 * DAY
RESYNC_EVERY = 10  # fresh calls between reads of the account's count

# API-Football league ids of other first tiers shortlist candidates come from, by Transfermarkt competition id
# (the configured leagues carry their own in config/leagues.yaml). A club in a league missing here is skipped.
LEAGUES = {
    "GB2": 40,  # Championship
    "PO1": 94,  # Primeira Liga
    "BE1": 144,  # Jupiler Pro League
    "TR1": 203,  # Süper Lig
    "SC1": 179,  # Scottish Premiership
    "GR1": 197,  # Super League Greece
    "UKR1": 333,  # Ukrainian Premier League
    "DK1": 119,  # Danish Superliga
    "RU1": 235,  # Russian Premier League
    "A1": 218,  # Austrian Bundesliga
    "C1": 207,  # Swiss Super League
}


@dataclass(frozen=True)
class Query:
    team: int  # API-Football team id
    league: int  # API-Football league id
    season: int  # start year: 2024 = 2024-25
    tm_comp: str | None = None  # Transfermarkt competition id (name matching within the season's squads)
    tm_club: str | None = None  # Transfermarkt club id, when known

    def params(self, page: int = 1) -> dict[str, Any]:
        p: dict[str, Any] = {"team": self.team, "league": self.league, "season": self.season}
        return {**p, "page": page} if page > 1 else p


def _int(v: Any) -> int | None:
    try:
        return None if v in (None, "") else int(v)
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def parse_players(items: list[dict], q: Query, url: str, fetched_at: datetime) -> list[dict]:
    """Rows for one page: each player's league totals for the club (entries with minutes only)."""
    rows = []
    for item in items:
        p = item["player"]
        birth = (p.get("birth") or {}).get("date")
        for s in item.get("statistics") or []:
            if (s.get("league") or {}).get("id") != q.league or (s.get("team") or {}).get("id") != q.team:
                continue
            g, sh, gl, pa, ta, du, dr, fo, ca = (
                s.get(k) or {}
                for k in (
                    "games",
                    "shots",
                    "goals",
                    "passes",
                    "tackles",
                    "duels",
                    "dribbles",
                    "fouls",
                    "cards",
                )
            )
            if not (_int(g.get("minutes")) or 0):
                continue
            rows.append(
                {
                    "api_player_id": p["id"],
                    "season": q.season,
                    "league_id": q.league,
                    "team_id": q.team,
                    "player_name": p.get("name"),
                    "birth_date": date.fromisoformat(birth) if birth else None,
                    "team_name": s["team"].get("name"),
                    "league_name": s["league"].get("name"),
                    "league_country": s["league"].get("country"),
                    "domestic": True,
                    "position": g.get("position"),
                    "appearances": _int(g.get("appearences")),  # sic: the API's spelling
                    "lineups": _int(g.get("lineups")),
                    "minutes": _int(g.get("minutes")),
                    "rating": _float(g.get("rating")),
                    "shots": _int(sh.get("total")),
                    "shots_on": _int(sh.get("on")),
                    "goals": _int(gl.get("total")),
                    "assists": _int(gl.get("assists")),
                    "passes": _int(pa.get("total")),
                    "key_passes": _int(pa.get("key")),
                    "pass_accuracy": _int(pa.get("accuracy")),
                    "tackles": _int(ta.get("total")),
                    "blocks": _int(ta.get("blocks")),
                    "interceptions": _int(ta.get("interceptions")),
                    "duels": _int(du.get("total")),
                    "duels_won": _int(du.get("won")),
                    "dribbles": _int(dr.get("attempts")),
                    "dribbles_won": _int(dr.get("success")),
                    "dribbled_past": _int(dr.get("past")),
                    "fouls_drawn": _int(fo.get("drawn")),
                    "fouls_committed": _int(fo.get("committed")),
                    "yellow": _int(ca.get("yellow")),
                    "red": _int(ca.get("red")),
                    "saves": _int(gl.get("saves")),
                    "conceded": _int(gl.get("conceded")),
                    "source_url": url,
                    "fetched_at": fetched_at,
                }
            )
    return rows


class OutOfBudget(Exception):
    """Today's share of the API-Football quota is spent: the rest waits for the next run."""


class ApiFootballStatsAdapter(ApiFootballHistoryAdapter):
    """Nightly backfill of player season statistics (see the module docstring). Runs once per job (global) and
    walks every configured league itself, so the daily cap covers all of them."""

    name = "api_football_stats"
    requires = "api_football"
    client_name = "api_football"
    scope = "global"

    # -- budget ---------------------------------------------------------------------------------------------
    def _fetch(self, path: str, params: dict[str, Any]):
        """A cached response, or a fresh one while today's share lasts (else OutOfBudget)."""
        if not self.client.is_cached(self.base_url + path, params=params, ttl=TTL):
            left = self.client.budget.remaining()
            if self._out or (self._cap.remaining() or 0) <= 0 or (left is not None and left <= STATS_RESERVE):
                self._out = True
                raise OutOfBudget
        try:
            body, res = self.call(path, params, ttl=TTL)
        except BudgetExceeded as exc:
            self._out = True
            raise OutOfBudget from exc
        if not res.from_cache:
            self._cap.spend()
            self.counts["api_calls"] += 1
            if self.counts["api_calls"] % RESYNC_EVERY == 0:
                self.sync_budget()
        return body, res

    # -- the queue ------------------------------------------------------------------------------------------
    def league_teams(self, lg: str, season: int) -> list[Query]:
        """The league's clubs that season (one `teams` call, cached)."""
        ids = league_config(lg).get("provider_ids", {})
        league = int(ids["api_football"])
        body, _ = self._fetch("teams", {"league": league, "season": season})
        teams = sorted(int(t["team"]["id"]) for t in body.get("response") or [])
        return [Query(t, league, season, ids.get("transfermarkt")) for t in teams]

    def candidate_teams(self, season: int) -> list[Query]:
        """Clubs that shortlist candidates (published `scout` rows) played for that season in a league outside
        the configured ones: the club with his most league minutes in transfermarkt-datasets appearances, its
        API-Football id from Reep, the league's from LEAGUES."""
        tm_ids = self._candidate_tm_ids()
        if not tm_ids or not (TM_DIR / "appearances.parquet").is_file():
            return []
        covered = {league_config(lg).get("provider_ids", {}).get("transfermarkt") for lg in leagues()}
        apps = pd.read_parquet(
            TM_DIR / "appearances.parquet",
            columns=["player_id", "player_club_id", "date", "competition_id", "minutes_played"],
        )
        apps = apps[apps.player_id.astype(str).isin(tm_ids) & apps.competition_id.isin(LEAGUES)]
        d = pd.to_datetime(apps.date)
        apps = apps[(d >= f"{season}-07-01") & (d < f"{season + 1}-07-01")]
        if apps.empty:
            return []
        mins = (
            apps.groupby(["player_id", "player_club_id", "competition_id"]).minutes_played.sum().reset_index()
        )
        main = mins.sort_values("minutes_played").drop_duplicates("player_id", keep="last")
        out: dict[int, Query] = {}
        for club, comp in main[["player_club_id", "competition_id"]].itertuples(index=False):
            if comp in covered:
                continue
            hit = self.ctx.reep.lookup("transfermarkt", "verein", str(club))
            team = _int(hit.ids.get("api_football/team")) if hit else None
            if team:
                out.setdefault(team, Query(team, LEAGUES[comp], season, comp, str(club)))
        return [out[t] for t in sorted(out)]  # a stable order: a run continues where the last stopped

    def _candidate_tm_ids(self) -> set[str]:
        try:
            ids = [
                r[0]
                for r in self.ctx.con.execute(
                    "SELECT DISTINCT entity_id FROM model_outputs WHERE model = 'scout' AND metric = 'candidate_name'"
                ).fetchall()
            ]
        except duckdb.CatalogException:  # a warehouse before the first publish
            return set()
        tm = {i.split(":", 1)[1] for i in ids if i.startswith("transfermarkt:")}
        reep = [i for i in ids if not i.startswith("transfermarkt:")]
        if reep and attach_catalogue(self.ctx.con):
            rows = self.ctx.con.execute(
                "SELECT external_id FROM reep.bridges WHERE provider = 'transfermarkt' AND namespace = 'spieler' "
                "AND reep_id IN (SELECT unnest(?))",
                [reep],
            ).fetchall()
            tm |= {str(r[0]) for r in rows}
        return tm

    def phases(self):
        """(label, a function giving that phase's queries), in the order the backfill takes them."""
        first, *rest = SEASONS
        configured = [lg for lg in leagues() if league_config(lg).get("provider_ids", {}).get("api_football")]
        for lg in configured:
            yield f"{lg} {first}", lambda lg=lg: self.league_teams(lg, first)
        yield f"candidates {first}", lambda: self.candidate_teams(first)
        for season in rest:
            for lg in configured:
                yield f"{lg} {season}", lambda lg=lg, season=season: self.league_teams(lg, season)

    # -- fetching -------------------------------------------------------------------------------------------
    def ingest(self) -> dict[str, int]:
        self.sync_budget()
        self._cap = DailyBudget(self.ctx.settings.cache_dir / "budget" / f"{self.name}.json", DAILY_CAP)
        self._cap._today = lambda: self.ctx.now.date()
        self._out = False
        self.counts = {"player_season_rows": 0, "rows_matched": 0, "api_calls": 0, "teams_done": 0}
        pending: list[str] = []
        for label, queries in self.phases():
            try:
                todo = queries()
            except OutOfBudget:
                pending.append(label)
                continue
            left = sum(not self._team(q) for q in todo)
            if left:
                pending.append(f"{label} ({left} clubs)")
        left = self.client.budget.remaining()
        log.info(
            "api_football_stats: %d rows (%d with a Transfermarkt id), %d calls, %d clubs complete; pending: %s; "
            "%s requests left",
            self.counts["player_season_rows"],
            self.counts["rows_matched"],
            self.counts["api_calls"],
            self.counts["teams_done"],
            ", ".join(pending) or "none",
            "unknown" if left is None else left,
        )
        return {**self.counts, "phases_pending": len(pending)}

    def _team(self, q: Query) -> bool:
        """Read (or fetch) a club's pages; True when all of them are in."""
        page, total = 1, 1
        while page <= total:
            try:
                body, res = self._fetch("players", q.params(page))
            except OutOfBudget:
                return False
            total = min(MAX_PAGE, int((body.get("paging") or {}).get("total") or 1))
            if page == 1 and int((body.get("paging") or {}).get("total") or 1) > MAX_PAGE:
                log.warning(
                    "api_football_stats: team %s has more than %d pages; the rest is out of reach",
                    q.team,
                    MAX_PAGE,
                )
            rows = self.map_players(parse_players(body.get("response") or [], q, res.url, res.fetched_at), q)
            self.counts["player_season_rows"] += upsert(self.ctx.con, "player_season_stats", rows)
            self.counts["rows_matched"] += sum(1 for r in rows if r["tm_player_id"])
            page += 1
        self.counts["teams_done"] += 1
        return True

    # -- matching -------------------------------------------------------------------------------------------
    def map_players(self, rows: list[dict], q: Query) -> list[dict]:
        """Transfermarkt id per row: Reep's bridge; else the one Transfermarkt player born that day whose name
        matches; else the name within the club's squad that season."""
        for r in rows:
            r["reep_id"] = r["tm_player_id"] = r["match_how"] = None
            hit = self.ctx.reep.lookup("api_football", "player", r["api_player_id"])
            if hit:
                r["reep_id"] = hit.reep_id
                if hit.ids.get("transfermarkt/spieler"):
                    r["tm_player_id"], r["match_how"] = hit.ids["transfermarkt/spieler"], "reep"
                    continue
            if r["birth_date"]:
                tm = best_player(r["player_name"] or "", self._born().get(r["birth_date"], []))
                if tm:
                    r["tm_player_id"], r["match_how"] = tm, "birth_date"
                    continue
            for squad in self._squads(q, r["team_name"]):
                tm = squad_player(r["player_name"] or "", squad)
                if tm:
                    r["tm_player_id"], r["match_how"] = tm, "name_in_club"
                    break
        return rows

    def _born(self) -> dict[date, list[tuple[str, str]]]:
        if not hasattr(self, "_by_birth"):
            self._by_birth: dict[date, list[tuple[str, str]]] = {}
            if (TM_DIR / "players.parquet").is_file():
                pl = pd.read_parquet(
                    TM_DIR / "players.parquet", columns=["player_id", "name", "date_of_birth"]
                )
                for pid, name, dob in pl.dropna(subset=["date_of_birth"]).itertuples(index=False):
                    self._by_birth.setdefault(pd.Timestamp(dob).date(), []).append((str(pid), name))
        return self._by_birth

    def _squads(self, q: Query, team: str | None) -> list[list[tuple[str, str]]]:
        """The club's Transfermarkt squad that season and over the seasons around it (`season_squads`)."""
        if not q.tm_comp or not (TM_DIR / "appearances.parquet").is_file():
            return []
        cache = self.__dict__.setdefault("_season_squads", {})
        key = (q.tm_comp, q.season)
        if key not in cache:
            apps = pd.read_parquet(
                TM_DIR / "appearances.parquet",
                columns=["player_id", "player_club_id", "date", "competition_id"],
            )
            players = pd.read_parquet(TM_DIR / "players.parquet", columns=["player_id", "name"])
            clubs = pd.read_parquet(TM_DIR / "clubs.parquet", columns=["club_id", "name"])
            cache[key] = season_squads(apps, players, clubs, q.tm_comp, q.season)
        league_clubs, by_club = cache[key]
        club = q.tm_club or self._tm_club(q.team, team or "", league_clubs)
        return by_club.get(club, [])
