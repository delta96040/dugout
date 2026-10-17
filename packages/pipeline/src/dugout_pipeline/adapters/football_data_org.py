"""football-data.org v4 (free tier: 12 competitions incl. PL, 10 calls/min, scores slightly delayed).

Premier League: second independent source for fixtures, results and standings; also head coaches and venues.
Leagues FPL does not cover (no `provider_ids.fpl`): the source of the clubs, squads and fixtures too. Clubs are
matched to Transfermarkt (and so Reep) ids by name within the league's country, players by date of birth and name
against transfermarkt-datasets (matching.py); a club or player without a confident match keeps a
football-data.org id.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from dugout_pipeline.adapters.base import HOUR, TIER_DATA, SourceAdapter
from dugout_pipeline.adapters.fpl import _eid, _ts, match_id
from dugout_pipeline.adapters.transfermarkt import TM_DIR
from dugout_pipeline.clubs import slugify
from dugout_pipeline.db import upsert
from dugout_pipeline.ids import CLUB_PROVIDERS, PLAYER_PROVIDERS
from dugout_pipeline.matching import best_club, best_player

POSITIONS = {"Goalkeeper": "GK", "Defence": "DEF", "Midfield": "MID", "Offence": "FWD"}
STATUS = {
    "FINISHED": "finished",
    "AWARDED": "finished",
    "IN_PLAY": "live",
    "PAUSED": "live",
    "LIVE": "live",
    "POSTPONED": "postponed",
    "SUSPENDED": "postponed",
    "CANCELLED": "postponed",
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
}


class FootballDataOrgAdapter(SourceAdapter):
    name = "football_data_org"
    requires = "football_data_org"
    tier = TIER_DATA
    ttl = 3 * HOUR
    rate, per, burst = 8, 60.0, 1  # 10/min allowed; stay under
    key_setting = "football_data_org_key"
    respect_robots = False  # keyed API governed by its terms (robots.txt applies to crawling web pages)
    required = True
    base_url = "https://api.football-data.org/v4/"

    def auth_headers(self) -> dict[str, str]:
        return {"X-Auth-Token": self.api_key}

    def ingest(self) -> dict[str, int]:
        ctx, cfg = self.ctx, self.ctx.league_cfg
        ctx.load_clubs()
        comp, year = cfg["provider_ids"]["football_data_org"], cfg["season_start_year"]
        counts: dict[str, int] = {}

        teams = self.client.get(
            f"{self.base_url}competitions/{comp}/teams", params={"season": year}, ttl=24 * HOUR
        )
        owns = not cfg["provider_ids"].get("fpl")  # no FPL: this source makes the league's clubs and squads
        if owns:
            clubs, club_ids = self.parse_new_clubs(teams.json())
            counts["clubs"] = upsert(ctx.con, "clubs", clubs)
            upsert(ctx.con, "entity_ids", club_ids)
            ctx.load_clubs()
        ids, managers, venues = self.parse_teams(teams.json(), teams.fetched_at)
        counts["entity_ids"] = upsert(ctx.con, "entity_ids", ids)
        counts["managers"] = upsert(ctx.con, "managers", managers)
        if owns:
            players, player_ids = self.parse_squads(teams.json())
            if players:  # a response without squads must not empty the league
                self.release_departed({p["player_id"] for p in players})
            counts["players"] = upsert(ctx.con, "players", players)
            counts["entity_ids"] += upsert(ctx.con, "entity_ids", player_ids)
        for slug, (venue, address) in venues.items():
            ctx.con.execute(
                "UPDATE clubs SET venue = coalesce(venue, ?), address = ? WHERE club_id = ?",
                [venue, address, slug],
            )
        ctx.load_clubs()

        matches = self.client.get(f"{self.base_url}competitions/{comp}/matches", params={"season": year})
        obs = self.parse_matches(matches.json(), cfg["season"], matches.fetched_at)
        counts["match_observations"] = upsert(ctx.con, "match_observations", obs)
        if owns:
            fixtures = self.parse_fixtures(matches.json(), cfg["season"], matches.fetched_at)
            counts["matches"] = upsert(ctx.con, "matches", fixtures)
        counts["evidence_ledger"] = self.record_scores(matches.json(), cfg["season"], matches)

        table = self.client.get(f"{self.base_url}competitions/{comp}/standings", params={"season": year})
        rows = self.parse_standings(table.json(), cfg["season"], table.fetched_at)
        counts["standings"] = upsert(ctx.con, "standings", rows)
        counts["evidence_ledger"] += self.record_standings(table.json(), cfg["season"], table)
        return counts

    # ---- leagues without FPL: clubs, squads, fixtures ------------------------------------------------------
    def parse_new_clubs(self, doc) -> tuple[list[dict], list[dict]]:
        """A club keeps the slug it first got (by its football-data.org id). A new club is named by its Reep label
        when it matches a Transfermarkt club; its slug gets the league's `slug_suffix` if another league already
        uses it, so slugs never collide across leagues."""
        cfg, con, league = self.ctx.league_cfg, self.ctx.con, self.ctx.league
        known = dict(
            con.execute(
                """SELECT external_id, dugout_id FROM entity_ids WHERE entity_type = 'club'
                   AND provider = 'football_data_org' AND namespace = 'team'"""
            ).fetchall()
        )
        taken = dict(con.execute("SELECT club_id, league FROM clubs").fetchall())
        overrides = {str(k): str(v) for k, v in (cfg.get("transfermarkt_clubs") or {}).items()}
        # the league's own clubs in transfermarkt-datasets first (canonical names), then Reep's clubs in the
        # country for a club new to the division (e.g. promoted for the first time)
        tiers = [
            self._tm_clubs(cfg["provider_ids"].get("transfermarkt")),
            self.ctx.reep.teams(cfg["country"]),
        ]
        clubs, ids = [], []
        for t in doc["teams"]:
            fid = str(t["id"])
            tm = overrides.get(fid)
            names = [t.get("name") or "", t.get("shortName") or ""]
            for candidates in tiers if tm is None else []:
                if hit := best_club(names, candidates, prefer={c[2] for c in tiers[0]}):
                    tm = hit[2]
                    break
            m = self.ctx.reep.lookup("transfermarkt", "verein", tm) if tm else None
            name = m.label if m and m.label else (t.get("shortName") or t["name"])
            slug = known.get(fid)
            if slug is None:
                slug = slugify(name)
                if taken.get(slug, league) != league:
                    slug = f"{slug}-{cfg.get('slug_suffix') or slugify(cfg['country'])}"
                taken[slug] = league
            clubs.append(
                {
                    "club_id": slug,
                    "reep_id": m.reep_id if m else None,
                    "name": name,
                    "short_name": t.get("tla"),
                    "league": league,
                    "season": cfg["season"],
                    "updated_at": self.ctx.now,
                }
            )
            ids.append(_eid("club", slug, "football_data_org", "team", fid, m))
            if tm:
                ids.append(_eid("club", slug, "transfermarkt", "verein", tm, m))
            if m:
                for prov, ns in CLUB_PROVIDERS.items():
                    if ext := m.ids.get(f"{prov}/{ns}"):
                        ids.append(_eid("club", slug, prov, ns, ext, m))
        return clubs, ids

    def _tm_clubs(self, comp: str | None) -> list[tuple[str, str, str]]:
        """(None, name, Transfermarkt id) of every club transfermarkt-datasets has in the competition."""
        path = TM_DIR / "clubs.parquet"
        if not comp or not path.exists():
            return []
        return self.ctx.con.execute(
            """SELECT NULL, name, CAST(club_id AS VARCHAR) FROM read_parquet(?)
               WHERE domestic_competition_id = ? ORDER BY last_season DESC""",
            [str(path), comp],
        ).fetchall()

    def _tm_players(self, dobs: set[str]) -> dict[str, list[tuple[str, str]]]:
        """transfermarkt-datasets players (CC0, saved by the history job) by date of birth."""
        path = TM_DIR / "players.parquet"
        if not dobs or not path.exists():
            return {}
        rows = self.ctx.con.execute(
            """SELECT strftime(CAST(date_of_birth AS DATE), '%Y-%m-%d') AS dob, CAST(player_id AS VARCHAR), name
               FROM read_parquet(?) WHERE list_contains(?, strftime(CAST(date_of_birth AS DATE), '%Y-%m-%d'))""",
            [str(path), sorted(dobs)],
        ).fetchall()
        out: dict[str, list[tuple[str, str]]] = {}
        for dob, tm, name in rows:
            out.setdefault(dob, []).append((tm, name))
        return out

    def parse_squads(self, doc) -> tuple[list[dict], list[dict]]:
        squads = [(self._slug(t), p) for t in doc["teams"] for p in t.get("squad") or []]
        tm_by_dob = self._tm_players({p["dateOfBirth"][:10] for _, p in squads if p.get("dateOfBirth")})
        players, ids = [], []
        for slug, p in squads:
            if not slug:
                continue
            dob = (p.get("dateOfBirth") or "")[:10]
            tm = best_player(p["name"], tm_by_dob.get(dob, [])) if dob else None
            m = self.ctx.reep.lookup("transfermarkt", "spieler", tm) if tm else None
            pid = m.reep_id if m else f"football_data_org:{p['id']}"
            players.append(
                {
                    "player_id": pid,
                    "reep_id": m.reep_id if m else None,
                    "name": p["name"],
                    "web_name": None,
                    "birth_date": _date(dob or None),
                    "nationality": p.get("nationality"),
                    "position": POSITIONS.get(p.get("position") or ""),
                    "club_id": slug,
                    "updated_at": self.ctx.now,
                }
            )
            ids.append(_eid("player", pid, "football_data_org", "person", p["id"], m))
            if tm:
                ids.append(_eid("player", pid, "transfermarkt", "spieler", tm, m))
            if m:  # API-Football ids among them: lineups and absentee lists attach to the same player
                for prov, ns in PLAYER_PROVIDERS.items():
                    if (ext := m.ids.get(f"{prov}/{ns}")) and prov != "transfermarkt":
                        ids.append(_eid("player", pid, prov, ns, ext, m))
        return players, ids

    def release_departed(self, current: set[str]) -> None:
        """Players no longer in any of the league's squads leave their club (like FPL's "has joined" players)."""
        self.ctx.con.execute(
            """UPDATE players SET club_id = NULL WHERE club_id IN (SELECT club_id FROM clubs WHERE league = ?)
               AND NOT list_contains(?, player_id)""",
            [self.ctx.league, sorted(current)],
        )

    def parse_fixtures(self, doc, season: str, fetched_at: datetime) -> list[dict]:
        rows = []
        for m in doc["matches"]:
            home, away = self._slug(m["homeTeam"]), self._slug(m["awayTeam"])
            if not home or not away:
                continue
            ft = m["score"]["fullTime"]
            rows.append(
                {
                    "match_id": match_id(self.ctx.league, season, home, away),
                    "league": self.ctx.league,
                    "season": season,
                    "gameweek": m.get("matchday"),
                    "kickoff_utc": _ts(m.get("utcDate")),
                    "home_club_id": home,
                    "away_club_id": away,
                    "status": STATUS.get(m["status"], m["status"].lower()),
                    "home_goals": ft["home"],
                    "away_goals": ft["away"],
                    "updated_at": fetched_at,
                }
            )
        return rows

    def _slug(self, team: dict[str, Any]) -> str | None:
        return self.ctx.clubs.resolve(team.get("name") or "", "football_data_org/team", team.get("id"))

    def parse_teams(self, doc, fetched_at: datetime) -> tuple[list[dict], list[dict], dict[str, tuple]]:
        ids, managers, venues = [], [], {}
        for t in doc["teams"]:
            slug = self._slug(t)
            if not slug:
                continue
            ids.append(
                {
                    "entity_type": "club",
                    "dugout_id": slug,
                    "provider": "football_data_org",
                    "namespace": "team",
                    "external_id": str(t["id"]),
                    "reep_id": None,
                }
            )
            venues[slug] = (t.get("venue"), t.get("address"))
            coach = t.get("coach") or {}
            if coach.get("name"):
                managers.append(
                    {
                        "manager_id": f"football_data_org:{coach['id']}",
                        "reep_id": None,
                        "name": coach["name"],
                        "birth_date": _date(coach.get("dateOfBirth")),
                        "nationality": coach.get("nationality"),
                        "club_id": slug,
                        "start_date": _date((coach.get("contract") or {}).get("start")),
                        "source": self.name,
                        "updated_at": fetched_at,
                    }
                )
        return ids, managers, venues

    def parse_matches(self, doc, season: str, fetched_at: datetime) -> list[dict]:
        rows = []
        for m in doc["matches"]:
            home, away = self._slug(m["homeTeam"]), self._slug(m["awayTeam"])
            if not home or not away:
                continue
            ft = m["score"]["fullTime"]
            rows.append(
                {
                    "match_id": match_id(self.ctx.league, season, home, away),
                    "source": self.name,
                    "external_id": str(m["id"]),
                    "status": STATUS.get(m["status"], m["status"].lower()),
                    "home_goals": ft["home"],
                    "away_goals": ft["away"],
                    "fetched_at": fetched_at,
                }
            )
        return rows

    def parse_standings(self, doc, season: str, fetched_at: datetime) -> list[dict]:
        total = next(s for s in doc["standings"] if s["type"] == "TOTAL")
        rows = []
        for r in total["table"]:
            slug = self._slug(r["team"])
            if slug:
                rows.append(
                    {
                        "league": self.ctx.league,
                        "season": season,
                        "club_id": slug,
                        "source": self.name,
                        "position": r["position"],
                        "played": r["playedGames"],
                        "won": r["won"],
                        "drawn": r["draw"],
                        "lost": r["lost"],
                        "goals_for": r["goalsFor"],
                        "goals_against": r["goalsAgainst"],
                        "points": r["points"],
                        "form": r.get("form"),
                        "fetched_at": fetched_at,
                    }
                )
        return rows

    def record_scores(self, doc, season: str, res) -> int:
        n = 0
        for m in doc["matches"]:
            if m["status"] != "FINISHED":
                continue
            home, away = self._slug(m["homeTeam"]), self._slug(m["awayTeam"])
            if not home or not away:
                continue
            for side, key in (("home_goals", "home"), ("away_goals", "away")):
                self.ctx.ledger.record(
                    entity_type="match",
                    entity_id=match_id(self.ctx.league, season, home, away),
                    metric=side,
                    value=m["score"]["fullTime"][key],
                    unit="goals",
                    source=self.name,
                    source_tier=self.tier,
                    fetched_at=res.fetched_at,
                    source_url=res.url,
                    query={
                        "kind": "http_json",
                        "source": self.name,
                        "url": res.url,
                        "path": ["matches", {"match": {"id": m["id"]}}, "score", "fullTime", key],
                    },
                )
                n += 1
        return n

    def record_standings(self, doc, season: str, res) -> int:
        n = 0
        total_idx = next(i for i, s in enumerate(doc["standings"]) if s["type"] == "TOTAL")
        for r in doc["standings"][total_idx]["table"]:
            slug = self._slug(r["team"])
            if not slug:
                continue
            for metric in ("position", "points", "goalsFor", "goalsAgainst"):
                self.ctx.ledger.record(
                    entity_type="club",
                    entity_id=slug,
                    metric=f"league_{metric}",
                    value=r[metric],
                    unit=None,
                    source=self.name,
                    source_tier=self.tier,
                    fetched_at=res.fetched_at,
                    source_url=res.url,
                    query={
                        "kind": "http_json",
                        "source": self.name,
                        "url": res.url,
                        "path": [
                            "standings",
                            total_idx,
                            "table",
                            {"match": {"team.id": r["team"]["id"]}},
                            metric,
                        ],
                    },
                )
                n += 1
        return n


def _date(s: str | None) -> date | None:
    """Accepts YYYY-MM-DD or YYYY-MM (contract dates)."""
    if not s:
        return None
    return date.fromisoformat(s if len(s) > 7 else f"{s}-01")
