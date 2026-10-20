"""API-Football v3 (api-sports.io), free plan.

Free-plan reality (checked 2026-10-15): 100 requests/day; full seasons only for 2022–2024; for the current
season only a rolling date window of yesterday..tomorrow. So nightly we use date-window queries
(fixtures, injuries, lineups, per-fixture statistics incl. xG) and backfill 2022–2024 slowly for training.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from dugout_pipeline.adapters.base import DAY, HOUR, TIER_DATA, IngestContext, SourceAdapter
from dugout_pipeline.adapters.fpl import match_id
from dugout_pipeline.adapters.transfermarkt import TM_DIR
from dugout_pipeline.clubs import ClubResolver, normalise
from dugout_pipeline.db import upsert
from dugout_pipeline.http import BudgetExceeded, FetchError
from dugout_pipeline.matching import best_club, squad_player
from dugout_pipeline.settings import REPO_ROOT

log = logging.getLogger(__name__)

INJURY_DIR = REPO_ROOT / "data" / "raw" / "api_football"

STATUS = {
    "FT": "finished",
    "AET": "finished",
    "PEN": "finished",
    "AWD": "finished",
    "WO": "finished",
    "PST": "postponed",
    "CANC": "postponed",
    "ABD": "postponed",
    "SUSP": "postponed",
    "NS": "scheduled",
    "TBD": "scheduled",
}
BUDGET_RESERVE = 10  # leave headroom for manual debugging and the 4-hourly injuries job
# Leagues without FPL take only the lineups of yesterday's finished matches (one call each) and the absentee
# lists (already fetched for every league by date): they stop while this many requests are left for the
# Premier League's 4-hourly injuries runs.
OTHER_LEAGUES_RESERVE = 30
KICKOFF_RESERVE = 15


def kickoff_fixtures(ctx: IngestContext) -> list[tuple[str, str | None, str, str, datetime]]:
    """Upcoming PL fixtures only; reuse the provider IDs already discovered by the nightly."""
    if ctx.league != "premier_league":
        raise ValueError("kickoff-lineups only supports premier_league")
    return ctx.con.execute(
        """SELECT m.match_id, o.external_id, m.home_club_id, m.away_club_id, m.kickoff_utc
           FROM matches m LEFT JOIN match_observations o
             ON m.match_id = o.match_id AND o.source = 'api_football'
           WHERE m.league = 'premier_league' AND m.status = 'scheduled'
             AND m.kickoff_utc > ? AND m.kickoff_utc <= ? ORDER BY m.kickoff_utc, m.match_id""",
        [ctx.now, ctx.now + timedelta(minutes=60)],
    ).fetchall()


class ApiFootballAdapter(SourceAdapter):
    name = "api_football"
    requires = "api_football"
    tier = TIER_DATA
    ttl = 1 * HOUR
    rate, per, burst = 1, 7.0, 1  # free plan also caps per-minute calls; ~8/min is safe
    daily_limit = 100 - BUDGET_RESERVE
    key_setting = "api_football_key"
    respect_robots = False  # keyed API governed by its terms
    base_url = "https://v3.football.api-sports.io/"

    def auth_headers(self) -> dict[str, str]:
        return {"x-apisports-key": self.api_key}

    def call(self, path: str, params: dict[str, Any], ttl: float | None = None):
        res = self.client.get(self.base_url + path, params=params, ttl=ttl)
        body = res.json()
        errors = body.get("errors")
        if errors and (not isinstance(errors, list) or errors) and getattr(res, "from_cache", False):
            # an error replayed from the cache (e.g. an old key's "suspended"): drop it and ask the API again
            self.client.forget(res)
            res = self.client.get(self.base_url + path, params=params, ttl=ttl)
            body = res.json()
            errors = body.get("errors")
        if errors and (not isinstance(errors, list) or errors):
            self.client.forget(res)  # never replay an error (quota, suspended key) from the cache
            raise RuntimeError(f"api_football {path}: {errors}")
        return body, res

    def ingest(self, today: date | None = None, with_lineups: bool = True) -> dict[str, int]:
        ctx = self.ctx
        ctx.load_clubs()
        today = today or ctx.now.date()
        league_id = ctx.league_cfg["provider_ids"]["api_football"]
        counts = {"match_observations": 0, "lineups": 0, "injuries": 0, "evidence_ledger": 0}
        light = not ctx.league_cfg["provider_ids"].get("fpl")  # other leagues: lineups + absentees only

        for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
            ttl = 7 * DAY if d < today else 1 * HOUR
            body, res = self.call("fixtures", {"date": d.isoformat()}, ttl=ttl)
            fixtures = [f for f in body["response"] if f["league"]["id"] == league_id]
            counts["match_observations"] += upsert(
                ctx.con, "match_observations", self.parse_fixtures(fixtures, res.fetched_at)
            )
            for f in fixtures:
                status = f["fixture"]["status"]["short"]
                mid = self._match_id(f)
                if mid is None:
                    continue
                if light:
                    if STATUS.get(status) == "finished" and self._lineups_affordable():
                        try:
                            lu, lres = self.call(
                                "fixtures/lineups", {"fixture": f["fixture"]["id"]}, ttl=30 * DAY
                            )
                        except BudgetExceeded:
                            continue
                        # a re-parse may name players it could not name before: replace, do not add
                        ctx.con.execute(
                            "DELETE FROM lineups WHERE match_id = ? AND source = ?", [mid, self.name]
                        )
                        counts["lineups"] += upsert(
                            ctx.con, "lineups", self.parse_lineups(mid, lu, lres.fetched_at)
                        )
                    continue
                if STATUS.get(status) == "finished":
                    stats, sres = self.call(
                        "fixtures/statistics", {"fixture": f["fixture"]["id"]}, ttl=30 * DAY
                    )
                    counts["match_observations"] += upsert(
                        ctx.con, "match_observations", self.parse_xg(mid, f, stats, sres.fetched_at)
                    )
                    counts["evidence_ledger"] += self.record_score(mid, f, res)
                if with_lineups and (STATUS.get(status) == "finished" or self._near_kickoff(f)):
                    lu, lres = self.call(
                        "fixtures/lineups",
                        {"fixture": f["fixture"]["id"]},
                        ttl=30 * DAY if STATUS.get(status) == "finished" else 10 * 60,
                    )
                    counts["lineups"] += upsert(
                        ctx.con, "lineups", self.parse_lineups(mid, lu, lres.fetched_at)
                    )

        for d in (today, today + timedelta(days=1)):
            body, res = self.call("injuries", {"date": d.isoformat()}, ttl=2 * HOUR)
            rows = self.parse_injuries(
                [i for i in body["response"] if i["league"]["id"] == league_id], res.fetched_at
            )
            counts["injuries"] += upsert(ctx.con, "injuries", rows)
        return counts

    def refresh_kickoff_lineups(self) -> list[str]:
        """Fetch only due XIs, keep the shared reserve, and propagate every refresh failure."""
        fixtures = kickoff_fixtures(self.ctx)
        if not fixtures:
            return []
        if any(not fid or not fid.isdigit() or int(fid) <= 0 for _, fid, *_ in fixtures):
            raise ValueError("due fixture missing API-Football ID; refresh the nightly warehouse first")
        self.ctx.load_clubs()
        # CachedClient accounts once per request, not per retry. Retry on the next scheduled run instead.
        self.client.max_retries = 0
        confirmed: set[str] = set()
        for mid, fid, home, away, kickoff in fixtures:
            clubs = {home, away}
            old = (
                self.ctx.con.execute(
                    "SELECT * FROM lineups WHERE match_id = ? AND source = ?", [mid, self.name]
                )
                .df()
                .to_dict("records")
            )
            if self._complete_kickoff_lineup(old, clubs, kickoff):
                confirmed.update(clubs)
                continue
            params = {"fixture": fid}
            # A cached provider error can be discarded and re-fetched by call(), so reserve before it too.
            self._check_kickoff_budget()
            body, res = self.call("fixtures/lineups", params, ttl=10 * 60)
            if body.get("response") == []:  # expected before the teams release their XIs
                continue
            rows = self.parse_lineups(mid, body, res.fetched_at)
            if not self._complete_kickoff_lineup(rows, clubs, kickoff):
                self.client.forget(res)
                raise ValueError(f"incomplete confirmed lineup for {mid}")
            self.ctx.con.execute("BEGIN TRANSACTION")
            try:
                self.ctx.con.execute(
                    "DELETE FROM lineups WHERE match_id = ? AND source = ?", [mid, self.name]
                )
                upsert(self.ctx.con, "lineups", rows)
                self.ctx.con.execute("COMMIT")
            except Exception:
                self.ctx.con.execute("ROLLBACK")
                raise
            confirmed.update(clubs)
        return sorted(confirmed)

    def _check_kickoff_budget(self) -> None:
        try:
            usage = json.loads(self.client.budget.path.read_text())
            if not isinstance(usage, dict) or not usage:
                raise ValueError("missing shared request counter")
            for day, used in usage.items():
                date.fromisoformat(day)
                if not isinstance(used, int) or isinstance(used, bool) or used < 0:
                    raise ValueError("invalid shared request counter")
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("cannot trust shared API-Football request counter") from exc
        if self.client.budget.remaining() <= KICKOFF_RESERVE:
            raise BudgetExceeded("kickoff refresh preserves 15 requests for other jobs")

    def _complete_kickoff_lineup(self, rows: list[dict], clubs: set[str], kickoff: datetime) -> bool:
        starters = [r for r in rows if r["starter"]]
        if len(starters) != 22 or {r["club_id"] for r in rows} != clubs:
            return False
        if len({(r["club_id"], r["player_id"]) for r in rows}) != len(rows):
            return False
        known = {
            pid: club
            for pid, club, name in self.ctx.con.execute(
                "SELECT player_id, club_id, name FROM players"
            ).fetchall()
            if name
        }
        if any(known.get(r["player_id"]) != r["club_id"] for r in starters):
            return False
        for club in clubs:
            xi = [r for r in starters if r["club_id"] == club]
            if (
                len(xi) != 11
                or len({r["player_id"] for r in xi}) != 11
                or any(r["position"] not in ("G", "D", "M", "F") for r in xi)
                or sum(r["position"] == "G" for r in xi) != 1
                or len({r["fetched_at"] for r in xi}) != 1
                or len({r["formation"] for r in xi}) != 1
                or not xi[0]["formation"]
            ):
                return False
            formation = xi[0]["formation"]
            if (
                not re.fullmatch(r"[1-5](?:-[1-5]){1,4}", formation)
                or sum(map(int, formation.split("-"))) != 10
            ):
                return False
            fetched = xi[0]["fetched_at"]
            # ctx.now precedes the HTTP fetch; use the current clock for the completed response too.
            if not kickoff - timedelta(minutes=75) <= fetched <= max(self.ctx.now, datetime.now(UTC)):
                return False
        return True

    def _lineups_affordable(self) -> bool:
        left = self.client.budget.remaining()
        return left is None or left > OTHER_LEAGUES_RESERVE

    def _near_kickoff(self, f: dict[str, Any]) -> bool:
        ko = datetime.fromisoformat(f["fixture"]["date"])
        return timedelta(minutes=-90) <= ko - self.ctx.now <= timedelta(minutes=75)

    resolver: ClubResolver | None = None

    def _slug(self, team: dict[str, Any]) -> str | None:
        hit = (self.resolver or self.ctx.clubs).resolve(team["name"], "api_football/team", team["id"])
        if hit or self.ctx.league_cfg["provider_ids"].get("fpl"):
            return hit
        # leagues whose clubs come from football-data.org: Reep lacks some API-Football ids ("1. FC Köln",
        # "Deportivo La Coruna"); match the name against the league's clubs and remember the id
        clubs = self.ctx.con.execute(
            "SELECT club_id, name, club_id FROM clubs WHERE league = ?", [self.ctx.league]
        ).fetchall()
        best = best_club([team["name"]], clubs)
        if best:
            self._remember("club", best[0], "team", team["id"])
            self.ctx.clubs.add(best[0], "api_football/team", team["id"])
        return best[0] if best else None

    def _remember(self, entity_type: str, dugout_id: str, ns: str, ext: Any) -> None:
        upsert(
            self.ctx.con,
            "entity_ids",
            [
                {
                    "entity_type": entity_type,
                    "dugout_id": dugout_id,
                    "provider": "api_football",
                    "namespace": ns,
                    "external_id": str(ext),
                    "reep_id": None,
                }
            ],
        )

    def _squad_player(self, club: str | None, api_id: int | None, name: str) -> str | None:
        """A lineup or absentee name matched within the club's own squad: every word of the name (initials aside,
        "J. Bellingham") must be in exactly one squad player's name. Remembered as an id."""
        if not club or not api_id or self.ctx.league_cfg["provider_ids"].get("fpl"):
            return None
        words = [w for w in normalise(name).split() if len(w) > 1]
        if not words:
            return None
        squad = self.ctx.con.execute(
            "SELECT player_id, name FROM players WHERE club_id = ?", [club]
        ).fetchall()
        hits = [pid for pid, n in squad if set(words) <= set(normalise(n).split())]
        if len(hits) != 1:
            return None
        self._remember("player", hits[0], "player", api_id)
        return hits[0]

    def _match_id(self, f: dict[str, Any]) -> str | None:
        home, away = self._slug(f["teams"]["home"]), self._slug(f["teams"]["away"])
        if not home or not away:
            return None
        season = f"{f['league']['season']}-{(f['league']['season'] + 1) % 100:02d}"
        return match_id(self.ctx.league, season, home, away)

    def _player(self, api_id: int | None, name: str, club: str | None = None) -> str:
        if api_id:
            row = self.ctx.con.execute(
                "SELECT dugout_id FROM entity_ids WHERE entity_type='player' AND provider='api_football' AND external_id=?",
                [str(api_id)],
            ).fetchone()
            if row:
                return row[0]
        return self._squad_player(club, api_id, name) or f"api_football:{api_id or name}"

    def parse_fixtures(self, fixtures: list[dict], fetched_at: datetime) -> list[dict]:
        rows = []
        for f in fixtures:
            mid = self._match_id(f)
            if mid:
                rows.append(
                    {
                        "match_id": mid,
                        "source": self.name,
                        "external_id": str(f["fixture"]["id"]),
                        "status": STATUS.get(f["fixture"]["status"]["short"], "live"),
                        "home_goals": f["goals"]["home"],
                        "away_goals": f["goals"]["away"],
                        "fetched_at": fetched_at,
                    }
                )
        return rows

    def parse_xg(self, mid: str, f: dict, stats: dict, fetched_at: datetime) -> list[dict]:
        xg: dict[int, float | None] = {}
        for team in stats["response"]:
            val = next((s["value"] for s in team["statistics"] if s["type"] == "expected_goals"), None)
            xg[team["team"]["id"]] = float(val) if val not in (None, "") else None
        return [
            {
                "match_id": mid,
                "source": self.name,
                "external_id": str(f["fixture"]["id"]),
                "status": STATUS.get(f["fixture"]["status"]["short"]),
                "home_goals": f["goals"]["home"],
                "away_goals": f["goals"]["away"],
                "home_xg": xg.get(f["teams"]["home"]["id"]),
                "away_xg": xg.get(f["teams"]["away"]["id"]),
                "fetched_at": fetched_at,
            }
        ]

    def parse_lineups(self, mid: str, lu: dict, fetched_at: datetime) -> list[dict]:
        rows = []
        for team in lu["response"]:
            slug = self._slug(team["team"])
            if not slug:
                continue
            for starter, key in ((True, "startXI"), (False, "substitutes")):
                for p in team.get(key) or []:
                    pl = p["player"]
                    rows.append(
                        {
                            "match_id": mid,
                            "club_id": slug,
                            "source": self.name,
                            "formation": team.get("formation"),
                            "player_id": self._player(pl.get("id"), pl["name"], slug),
                            "starter": starter,
                            "position": pl.get("pos"),
                            "grid": pl.get("grid"),
                            "fetched_at": fetched_at,
                        }
                    )
        return rows

    def parse_injuries(self, items: list[dict], fetched_at: datetime) -> list[dict]:
        rows = []
        for i in items:
            kind = (i["player"].get("type") or "").lower()
            club = self._slug(i["team"])
            rows.append(
                {
                    "player_id": self._player(i["player"].get("id"), i["player"]["name"], club),
                    "club_id": club,
                    "source": self.name,
                    "snapshot_date": fetched_at.date(),
                    "status": "doubtful" if "questionable" in kind else "unavailable",
                    "reason": i["player"].get("reason"),
                    "chance_next": None,
                    "news": i["player"].get("type"),
                    "news_added": None,
                    "expected_return": None,
                    "fetched_at": fetched_at,
                }
            )
        return rows

    def record_score(self, mid: str, f: dict, res) -> int:
        for side in ("home", "away"):
            self.ctx.ledger.record(
                entity_type="match",
                entity_id=mid,
                metric=f"{side}_goals",
                value=f["goals"][side],
                unit="goals",
                source=self.name,
                source_tier=self.tier,
                fetched_at=res.fetched_at,
                source_url=res.url,
                query={
                    "kind": "http_json",
                    "source": self.name,
                    "url": res.url,
                    "path": ["response", {"match": {"fixture.id": f["fixture"]["id"]}}, "goals", side],
                },
            )
        return 2

    def backfill_season(self, season: int, max_calls: int = 40) -> dict[str, int]:
        """Slowly backfill a free-plan season (2022–2024) for training: fixtures + per-fixture xG.

        Resumable: cached fixtures cost nothing, so repeated nightly runs walk through the season.
        Historical clubs (relegated sides) resolve by name, not only against the current 20.
        """
        league_id = self.ctx.league_cfg["provider_ids"]["api_football"]
        self.ctx.load_clubs()
        self.resolver = ClubResolver(
            aliases=self.ctx.clubs.aliases, by_provider=dict(self.ctx.clubs.by_provider)
        )
        body, res = self.call("fixtures", {"league": league_id, "season": season}, ttl=365 * DAY)
        rows, calls = self.parse_fixtures(body["response"], res.fetched_at), 0
        for f in body["response"]:
            mid = self._match_id(f)
            if not mid or STATUS.get(f["fixture"]["status"]["short"]) != "finished" or calls >= max_calls:
                continue
            try:
                stats, sres = self.call("fixtures/statistics", {"fixture": f["fixture"]["id"]}, ttl=365 * DAY)
            except BudgetExceeded:
                break
            calls += 0 if sres.from_cache else 1
            rows += self.parse_xg(mid, f, stats, sres.fetched_at)
        return {"match_observations": upsert(self.ctx.con, "match_observations", rows), "api_calls": calls}

    def injury_history(self, season: int) -> pd.DataFrame:
        """Free plan: a league's full-season injury/absence list for 2022-2024 (one call, cached for a year).
        Saved as Parquet for the injury-risk model, with Reep and Transfermarkt player ids where they are known
        (see `map_transfermarkt`)."""
        league_id = self.ctx.league_cfg["provider_ids"]["api_football"]
        body, _ = self.call("injuries", {"league": league_id, "season": season}, ttl=365 * DAY)
        rows = [
            {
                "api_player_id": i["player"]["id"],
                "player_name": i["player"]["name"],
                "type": i["player"]["type"],
                "reason": i["player"]["reason"],
                "api_team_id": i["team"]["id"],
                "team": i["team"]["name"],
                "fixture_id": i["fixture"]["id"],
                "date": i["fixture"]["date"][:10],
                "season": season,
            }
            for i in body["response"]
        ]
        df = pd.DataFrame(rows, columns=INJURY_COLUMNS)
        df["league"] = self.ctx.league
        df = self.map_transfermarkt(df, season)
        INJURY_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(injury_file(self.ctx.league, season), index=False)
        return df

    def map_transfermarkt(self, df: pd.DataFrame, season: int) -> pd.DataFrame:
        """Reep id and Transfermarkt id per absentee. Reep's bridge first; else the name within his club's
        squad that season (players with a Transfermarkt appearance for the club; the seasons either side when he
        missed all of it), the club found by Reep or by name among the league's clubs that season. The free
        plan's injury list carries no date of birth, so a name two squad players share stays unmatched."""
        reep: dict[int, str] = {}
        tm: dict[int, str] = {}
        how: dict[int, str] = {}
        for api_id in df.api_player_id.dropna().unique():
            hit = self.ctx.reep.lookup("api_football", "player", int(api_id))
            if hit:
                reep[api_id] = hit.reep_id
                if hit.ids.get("transfermarkt/spieler"):
                    tm[api_id], how[api_id] = hit.ids["transfermarkt/spieler"], "reep"
        squads = self.tm_squads(season)
        if squads is not None:
            league_clubs, by_club = squads
            keys = ["api_player_id", "player_name", "api_team_id", "team"]
            for api_id, name, team_id, team in df[keys].drop_duplicates().itertuples(index=False):
                if api_id in tm:
                    continue
                club = self._tm_club(int(team_id), team, league_clubs)
                for squad in by_club.get(club, ()):
                    hit = squad_player(name, squad)
                    if hit:
                        tm[api_id], how[api_id] = hit, "name_in_club"
                        break
        df["reep_id"] = df.api_player_id.map(reep)
        df["tm_player_id"] = df.api_player_id.map(tm)
        df["match"] = df.api_player_id.map(how)
        return df

    def _tm_club(self, team_id: int, team: str, league_clubs: list[tuple[str, str, str]]) -> str | None:
        cache = self.__dict__.setdefault("_tm_clubs", {})
        if team_id not in cache:
            hit = self.ctx.reep.lookup("api_football", "team", team_id)
            tm = hit.ids.get("transfermarkt/verein") if hit else None
            if not tm:
                best = best_club([team], league_clubs)
                tm = best[2] if best else None
            cache[team_id] = tm
        return cache[team_id]

    def tm_squads(self, season: int) -> tuple[list[tuple[str, str, str]], dict[str, list]] | None:
        """(the league's clubs that season as (id, name, id), {club: [season squad, three-season squad]}) from
        transfermarkt-datasets (CC0), or None when its tables are not downloaded (the history job loads them first)."""
        comp = self.ctx.league_cfg["provider_ids"].get("transfermarkt")
        if not comp or not (TM_DIR / "appearances.parquet").is_file():
            log.warning("transfermarkt-datasets tables missing: absentees matched by Reep only")
            return None
        apps = pd.read_parquet(
            TM_DIR / "appearances.parquet", columns=["player_id", "player_club_id", "date", "competition_id"]
        )
        players = pd.read_parquet(TM_DIR / "players.parquet", columns=["player_id", "name"])
        clubs = pd.read_parquet(TM_DIR / "clubs.parquet", columns=["club_id", "name"])
        return season_squads(apps, players, clubs, comp, season)


def season_squads(
    apps: pd.DataFrame, players: pd.DataFrame, clubs: pd.DataFrame, comp: str, season: int
) -> tuple[list[tuple[str, str, str]], dict[str, list]]:
    """Clubs that played in `comp` in the season starting July `season`, and per club its squad that season and
    over the three seasons around it, as (Transfermarkt player id, name) lists."""
    apps = apps.assign(date=pd.to_datetime(apps.date), club=apps.player_club_id.astype(str))
    start, end = pd.Timestamp(f"{season}-07-01"), pd.Timestamp(f"{season + 1}-07-01")
    wide = apps[(apps.date >= start - pd.DateOffset(years=1)) & (apps.date < end + pd.DateOffset(years=1))]
    names = dict(zip(players.player_id.astype(str), players.name, strict=True))
    club_names = dict(zip(clubs.club_id.astype(str), clubs.name, strict=True))
    in_season = wide[(wide.date >= start) & (wide.date < end)]
    ids = sorted(in_season.loc[in_season.competition_id == comp, "club"].unique())

    def squad(rows: pd.DataFrame) -> dict[str, list[tuple[str, str]]]:
        return {
            c: [(str(p), names.get(str(p), "")) for p in g.player_id.unique()]
            for c, g in rows[rows.club.isin(ids)].groupby("club")
        }

    now, around = squad(in_season), squad(wide)
    return [(c, club_names.get(c, ""), c) for c in ids], {c: [now.get(c, []), around.get(c, [])] for c in ids}


# The history backfill stops while this many of the adapter's daily requests are left (its limit already holds
# BUDGET_RESERVE back): 30 + 10 leaves 40 of the account's 100 for the nightly's lineups and absentee lists.
HISTORY_RESERVE = 30
INJURY_COLUMNS = [
    "api_player_id",
    "player_name",
    "type",
    "reason",
    "api_team_id",
    "team",
    "fixture_id",
    "date",
    "season",
]


def injury_file(league: str, season: int) -> Path:
    """The Premier League's files keep their first names (the lineup backtest reads them)."""
    if league == "premier_league":
        return INJURY_DIR / f"injuries_{season}.parquet"
    return INJURY_DIR / f"injuries_{league}_{season}.parquet"


class ApiFootballHistoryAdapter(ApiFootballAdapter):
    """History job: the free plan's full-season injury lists (2022-2024) that the injury-risk model trains on,
    for every league with an API-Football id (the other leagues list it under `sources`). One call per
    league-season, cached for a year; shares API-Football's cache and daily budget. Stops with HISTORY_RESERVE
    requests left; a later run takes the remaining seasons (fetched ones come from the cache)."""

    name = "api_football_history"
    requires = "api_football"
    client_name = "api_football"
    seasons = (2022, 2023, 2024)  # the free plan's seasons

    def sync_budget(self) -> None:
        """Align today's count with the account's: CI and local runs share one quota, and a lost cache forgets
        the count. API-Football does not count calls to `status`."""
        try:
            body, _ = self.call("status", {}, ttl=0)
            used = int(body["response"]["requests"]["current"])
        except (BudgetExceeded, FetchError, RuntimeError, KeyError, TypeError, ValueError) as exc:
            log.warning("api_football status unavailable (%s); using the local count", type(exc).__name__)
            return
        self.client.budget.observe(used)

    def ingest(self) -> dict[str, int]:
        self.sync_budget()
        league_id = self.ctx.league_cfg["provider_ids"]["api_football"]
        rows, calls, matched, pending = 0, 0, 0, []
        for season in self.seasons:
            params = {"league": league_id, "season": season}
            cached = self.client.is_cached(self.base_url + "injuries", params=params, ttl=365 * DAY)
            left = self.client.budget.remaining()
            if not cached and left is not None and left <= HISTORY_RESERVE:
                pending.append(season)
                continue
            try:
                df = self.injury_history(season)
            except BudgetExceeded:
                pending.append(season)
                continue
            rows, calls = rows + len(df), calls + (0 if cached else 1)
            matched += int(df.tm_player_id.notna().sum())
        left = self.client.budget.remaining()
        log.info(
            "api_football_history %s: %d rows (%d with a Transfermarkt id), %d calls; seasons pending: %s; "
            "%s requests left today",
            self.ctx.league,
            rows,
            matched,
            calls,
            pending or "none",
            "unknown" if left is None else left,
        )
        return {
            "injury_history_rows": rows,
            "injury_rows_matched": matched,
            "api_calls": calls,
            "seasons_pending": len(pending),
        }
