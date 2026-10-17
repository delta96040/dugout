"""Fantasy Premier League API (unofficial, keyless, run by the Premier League).

Best free source for PL clubs, squads, availability (status / news / chance of playing), set-piece takers,
fixtures and per-gameweek player stats including Opta-derived xG. FPL `code` fields are Opta numeric ids,
which is how players and clubs are mapped to Reep ids.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from dugout_pipeline.adapters.base import DAY, HOUR, TIER_OFFICIAL, SourceAdapter
from dugout_pipeline.clubs import ClubResolver, slugify
from dugout_pipeline.db import upsert
from dugout_pipeline.ids import CLUB_PROVIDERS, PLAYER_PROVIDERS

POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
STATUS = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "ineligible",
}


def match_id(league: str, season: str, home: str, away: str) -> str:
    """Stable across providers and reschedules: each pair meets once per venue per league season."""
    return f"{league}:{season}:{home}:{away}"


def _f(x: Any) -> float | None:
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def _ts(x: str | None) -> datetime | None:
    return datetime.fromisoformat(x.replace("Z", "+00:00")) if x else None


def left_club(element: dict[str, Any]) -> bool:
    """FPL keeps players who moved on at their old club, flagged unavailable with news like "Has joined Valencia
    on loan for the rest of the season" or "Has joined Celta Vigo permanently": they are not in the squad."""
    return element.get("status") == "u" and bool(
        re.match(r"\s*has joined\b", element.get("news") or "", re.I)
    )


class FPLAdapter(SourceAdapter):
    name = "fpl"
    requires = "fpl"  # FPL only covers the Premier League
    tier = TIER_OFFICIAL
    ttl = 2 * HOUR
    rate, per, burst = 1.0, 2.0, 1  # one request every 2 s: polite for an unofficial API
    required = True
    base_url = "https://fantasy.premierleague.com/api/"

    def bootstrap(self) -> tuple[dict[str, Any], Any]:
        res = self.client.get(self.base_url + "bootstrap-static/")
        return res.json(), res

    def ingest(self) -> dict[str, int]:
        cfg, ctx = self.ctx.league_cfg, self.ctx
        season = cfg["season"]
        boot, res = self.bootstrap()
        counts: dict[str, int] = {}

        clubs, club_ids = self.parse_clubs(boot, season)
        counts["clubs"] = upsert(ctx.con, "clubs", clubs)
        counts["entity_ids"] = upsert(ctx.con, "entity_ids", club_ids)
        ctx.load_clubs()
        team_slug = {r["external_id"]: r["dugout_id"] for r in club_ids if r["provider"] == "fpl"}

        players, player_ids, fpl_to_player = self.parse_players(boot, team_slug)
        counts["players"] = upsert(ctx.con, "players", players)
        counts["entity_ids"] += upsert(ctx.con, "entity_ids", player_ids)
        counts["injuries"] = upsert(
            ctx.con, "injuries", self.parse_injuries(boot, team_slug, fpl_to_player, res.fetched_at)
        )
        counts["evidence_ledger"] = self.record_player_evidence(boot, fpl_to_player, res)
        counts["set_piece_notes"] = upsert(
            ctx.con, "set_piece_notes", self.parse_set_pieces(boot, team_slug, res.fetched_at)
        )

        fx_res = self.client.get(self.base_url + "fixtures/")
        fixtures = fx_res.json()
        matches, obs, fixture_match = self.parse_fixtures(fixtures, team_slug, season, fx_res.fetched_at)
        counts["matches"] = upsert(ctx.con, "matches", matches)
        counts["match_observations"] = upsert(ctx.con, "match_observations", obs)
        counts["evidence_ledger"] += self.record_score_evidence(fixtures, fixture_match, fx_res)

        finished_events = [e["id"] for e in boot["events"] if e["finished"] or e["is_current"]]
        stats = []
        for ev in finished_events:
            done = next(e for e in boot["events"] if e["id"] == ev)["finished"]
            live = self.client.get(f"{self.base_url}event/{ev}/live/", ttl=30 * DAY if done else 2 * HOUR)
            stats += self.parse_live(
                live.json(), fixture_match, fpl_to_player, team_slug, boot, live.fetched_at
            )
        counts["player_match_stats"] = upsert(ctx.con, "player_match_stats", stats)
        return counts

    # ---- parsers (pure; tested on recorded fixtures) -------------------------------------------------

    def parse_clubs(self, boot: dict[str, Any], season: str) -> tuple[list[dict], list[dict]]:
        clubs, ids, now = [], [], self.ctx.now
        by_alias = ClubResolver.from_config()  # FPL uses short names ("Man Utd", "Spurs")
        for t in boot["teams"]:
            m = self.ctx.reep.lookup("opta", "team_numeric", t["code"])
            name = m.label if m else t["name"]
            slug = slugify(name) if m else (by_alias.resolve(t["name"]) or slugify(t["name"]))
            clubs.append(
                {
                    "club_id": slug,
                    "reep_id": m.reep_id if m else None,
                    "name": name,
                    "short_name": t["short_name"],
                    "league": self.ctx.league,
                    "season": season,
                    "updated_at": now,
                }
            )
            ids.append(_eid("club", slug, "fpl", "team", t["id"], m))
            ids.append(_eid("club", slug, "opta", "team_numeric", t["code"], m))
            if m:
                for prov, ns in CLUB_PROVIDERS.items():
                    if ext := m.ids.get(f"{prov}/{ns}"):
                        ids.append(_eid("club", slug, prov, ns, ext, m))
        return clubs, ids

    def parse_players(
        self, boot: dict[str, Any], team_slug: dict[str, str]
    ) -> tuple[list[dict], list[dict], dict[int, str]]:
        players, ids, fpl_to_player = [], [], {}
        for e in boot["elements"]:
            m = self.ctx.reep.lookup("opta", "person_numeric", e["code"])
            pid = m.reep_id if m else f"opta:{e['code']}"
            fpl_to_player[e["id"]] = pid
            players.append(
                {
                    "player_id": pid,
                    "reep_id": m.reep_id if m else None,
                    "name": f"{e['first_name']} {e['second_name']}".strip(),
                    "web_name": e["web_name"],
                    "birth_date": date.fromisoformat(e["birth_date"]) if e.get("birth_date") else None,
                    "position": POSITIONS.get(e["element_type"]),
                    "club_id": None if left_club(e) else team_slug.get(str(e["team"])),
                    "updated_at": self.ctx.now,
                }
            )
            ids.append(_eid("player", pid, "fpl", "element", e["id"], m))
            ids.append(_eid("player", pid, "opta", "person_numeric", e["code"], m))
            if m:
                for prov, ns in PLAYER_PROVIDERS.items():
                    if ext := m.ids.get(f"{prov}/{ns}"):
                        ids.append(_eid("player", pid, prov, ns, ext, m))
        return players, ids, fpl_to_player

    def parse_injuries(self, boot, team_slug, fpl_to_player, fetched_at: datetime) -> list[dict]:
        rows = []
        for e in boot["elements"]:
            if (e["status"] == "a" and not e["news"]) or left_club(e):
                continue
            rows.append(
                {
                    "player_id": fpl_to_player[e["id"]],
                    "club_id": team_slug.get(str(e["team"])),
                    "source": self.name,
                    "snapshot_date": fetched_at.date(),
                    "status": STATUS.get(e["status"], e["status"]),
                    "reason": e["news"].split(" - ")[0] if e["news"] else None,
                    "chance_next": e["chance_of_playing_next_round"],
                    "news": e["news"] or None,
                    "news_added": _ts(e.get("news_added")),
                    "fetched_at": fetched_at,
                }
            )
        return rows

    def parse_set_pieces(self, boot, team_slug, fetched_at: datetime) -> list[dict]:
        rows = []
        kinds = {
            "penalties_order": "penalties",
            "direct_freekicks_order": "direct free kicks",
            "corners_and_indirect_freekicks_order": "corners & indirect free kicks",
        }
        for e in boot["elements"]:
            for field_, label in kinds.items():
                if e.get(field_):
                    rows.append(
                        {
                            "club_id": team_slug.get(str(e["team"])),
                            "source": self.name,
                            "fetched_at": fetched_at,
                            "note": f"{label}: {e['web_name']} (order {e[field_]})",
                        }
                    )
        return rows

    def parse_fixtures(
        self, fixtures, team_slug, season, fetched_at
    ) -> tuple[list[dict], list[dict], dict[int, str]]:
        matches, obs, fixture_match = [], [], {}
        for f in fixtures:
            home, away = team_slug.get(str(f["team_h"])), team_slug.get(str(f["team_a"]))
            if not home or not away:
                continue
            mid = match_id(self.ctx.league, season, home, away)
            fixture_match[f["id"]] = mid
            status = "finished" if f["finished"] else "live" if f["started"] else "scheduled"
            if f["kickoff_time"] is None:
                status = "postponed"
            matches.append(
                {
                    "match_id": mid,
                    "league": self.ctx.league,
                    "season": season,
                    "gameweek": f["event"],
                    "kickoff_utc": _ts(f["kickoff_time"]),
                    "home_club_id": home,
                    "away_club_id": away,
                    "status": status,
                    "home_goals": f["team_h_score"],
                    "away_goals": f["team_a_score"],
                    "updated_at": fetched_at,
                }
            )
            obs.append(
                {
                    "match_id": mid,
                    "source": self.name,
                    "external_id": str(f["id"]),
                    "status": status,
                    "home_goals": f["team_h_score"],
                    "away_goals": f["team_a_score"],
                    "fetched_at": fetched_at,
                }
            )
        return matches, obs, fixture_match

    def parse_live(self, live, fixture_match, fpl_to_player, team_slug, boot, fetched_at) -> list[dict]:
        team_of = {e["id"]: team_slug.get(str(e["team"])) for e in boot["elements"]}
        rows = []
        for el in live["elements"]:
            fixtures = [x["fixture"] for x in el.get("explain", [])]
            if not fixtures or el["stats"]["minutes"] == 0:
                continue
            single = len(fixtures) == 1
            for x in el["explain"]:
                mid = fixture_match.get(x["fixture"])
                if not mid:
                    continue
                vals = {s["identifier"]: s["value"] for s in x["stats"]}
                st = el["stats"]
                rows.append(
                    {
                        "player_id": fpl_to_player.get(el["id"], f"fpl:{el['id']}"),
                        "match_id": mid,
                        "source": self.name,
                        "club_id": team_of.get(el["id"]),
                        "minutes": vals.get("minutes", 0),
                        "goals": vals.get("goals_scored", 0),
                        "assists": vals.get("assists", 0),
                        "yellow": vals.get("yellow_cards", 0),
                        "red": vals.get("red_cards", 0),
                        # xG / xA / starts are only reported per gameweek; attribute them only when unambiguous.
                        "xg": _f(st.get("expected_goals")) if single else None,
                        "xa": _f(st.get("expected_assists")) if single else None,
                        "starts": st.get("starts") if single else None,
                        # gameweek-level extras, likewise only when the gameweek had a single fixture
                        **(
                            {
                                "saves": st.get("saves"),
                                "influence": _f(st.get("influence")),
                                "creativity": _f(st.get("creativity")),
                                "threat": _f(st.get("threat")),
                                "bps": st.get("bps"),
                                "xgc": _f(st.get("expected_goals_conceded")),
                                "tackles": st.get("tackles"),
                                "recoveries": st.get("recoveries"),
                                "cbi": st.get("clearances_blocks_interceptions"),
                                "defensive_contribution": st.get("defensive_contribution"),
                            }
                            if single
                            else {}
                        ),
                        "fetched_at": fetched_at,
                    }
                )
        return rows

    # ---- evidence -------------------------------------------------------------------------------------

    def record_player_evidence(self, boot, fpl_to_player, res) -> int:
        ledger, n = self.ctx.ledger, 0
        for e in boot["elements"]:
            if e["minutes"] == 0 and e["status"] == "a":
                continue
            pid = fpl_to_player[e["id"]]
            for metric, unit in (
                ("goals_scored", "goals"),
                ("assists", "assists"),
                ("minutes", "minutes"),
                ("expected_goals", "xG"),
                ("chance_of_playing_next_round", "%"),
            ):
                value = e.get(metric)
                if value is None:
                    continue
                ledger.record(
                    entity_type="player",
                    entity_id=pid,
                    metric=f"season_{metric}",
                    value=_f(value),
                    unit=unit,
                    source=self.name,
                    source_tier=self.tier,
                    fetched_at=res.fetched_at,
                    source_url=res.url,
                    query={
                        "kind": "http_json",
                        "source": self.name,
                        "url": res.url,
                        "path": ["elements", {"match": {"id": e["id"]}}, metric],
                    },
                )
                n += 1
        return n

    def record_score_evidence(self, fixtures, fixture_match, res) -> int:
        ledger, n = self.ctx.ledger, 0
        for f in fixtures:
            if not f["finished"] or f["id"] not in fixture_match:
                continue
            for side, key in (("home_goals", "team_h_score"), ("away_goals", "team_a_score")):
                ledger.record(
                    entity_type="match",
                    entity_id=fixture_match[f["id"]],
                    metric=side,
                    value=f[key],
                    unit="goals",
                    source=self.name,
                    source_tier=self.tier,
                    fetched_at=res.fetched_at,
                    source_url=res.url,
                    query={
                        "kind": "http_json",
                        "source": self.name,
                        "url": res.url,
                        "path": [{"match": {"id": f["id"]}}, key],
                    },
                )
                n += 1
        return n


def _eid(entity_type: str, dugout_id: str, provider: str, ns: str, ext: Any, m) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "dugout_id": dugout_id,
        "provider": provider,
        "namespace": ns,
        "external_id": str(ext),
        "reep_id": m.reep_id if m else None,
    }
