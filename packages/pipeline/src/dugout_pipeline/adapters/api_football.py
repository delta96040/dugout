"""API-Football v3 (api-sports.io), free plan.

Free-plan reality (checked 2026-10-15): 100 requests/day; full seasons only for 2022–2024; for the current
season only a rolling date window of yesterday..tomorrow. So nightly we use date-window queries
(fixtures, injuries, lineups, per-fixture statistics incl. xG) and backfill 2022–2024 slowly for training.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from dugout_pipeline.adapters.base import DAY, HOUR, TIER_DATA, SourceAdapter
from dugout_pipeline.adapters.fpl import match_id
from dugout_pipeline.clubs import ClubResolver, normalise
from dugout_pipeline.db import upsert
from dugout_pipeline.http import BudgetExceeded
from dugout_pipeline.ids import attach_catalogue
from dugout_pipeline.matching import best_club
from dugout_pipeline.settings import REPO_ROOT

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
        if errors and (not isinstance(errors, list) or errors):
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
        """Free plan: full-season injury/absence lists for 2022-2024 (one call each, cached for a year).
        Saved as Parquet for the injury-risk model; player ids are mapped to Reep where the register knows them."""
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
        df = pd.DataFrame(rows)
        if attach_catalogue(self.ctx.con):
            ids = df.api_player_id.astype(str).unique().tolist()
            m = self.ctx.con.execute(
                "SELECT external_id, reep_id FROM reep.bridges WHERE provider='api_football' AND namespace='player' "
                "AND external_id IN (SELECT unnest(?))",
                [ids],
            ).df()
            df = df.merge(
                m.rename(columns={"external_id": "api_player_id_s"}),
                left_on=df.api_player_id.astype(str),
                right_on="api_player_id_s",
                how="left",
            ).drop(columns=["key_0", "api_player_id_s"], errors="ignore")
        INJURY_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(INJURY_DIR / f"injuries_{season}.parquet", index=False)
        return df


class ApiFootballHistoryAdapter(ApiFootballAdapter):
    """History job: the free plan's full-season injury lists (2022-2024) that the injury-risk model trains on.
    One call per season, cached for a year; shares API-Football's cache and daily budget."""

    name = "api_football_history"
    requires = "api_football"
    client_name = "api_football"
    seasons = (2022, 2023, 2024)  # the free plan's seasons

    def ingest(self) -> dict[str, int]:
        rows = 0
        for season in self.seasons:
            rows += len(self.injury_history(season))
        return {"injury_history_rows": rows}
