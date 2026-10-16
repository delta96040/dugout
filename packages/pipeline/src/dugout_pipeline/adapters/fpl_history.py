"""Historical FPL data (vaastav/Fantasy-Premier-League, MIT; data originally from the FPL API).

The only free source of Opta-derived xG per player per fixture for past Premier League seasons (2022-23 on).
Used locally for model training and backtests; never redistributed. Credit + link back as the author asks.

Team xG for a match = the maximum `expected_goals_conceded` among the opposing players: anyone who played the
full match carries the whole-match figure. (Checked on 2024-25: mean |diff| vs summed player xG = 0.016.)
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

from dugout_pipeline.adapters.base import DAY, TIER_DATA, SourceAdapter
from dugout_pipeline.adapters.fpl import match_id
from dugout_pipeline.clubs import ClubResolver, slugify
from dugout_pipeline.db import upsert
from dugout_pipeline.ids import CLUB_PROVIDERS

SEASONS_WITH_XG = ["2022-23", "2023-24", "2024-25", "2025-26"]
# FPL source column -> our column; FPL started tracking these in 2025-26
DEFENSIVE = {
    "tackles": "tackles",
    "recoveries": "recoveries",
    "clearances_blocks_interceptions": "cbi",
    "defensive_contribution": "defensive_contribution",
}


class FPLHistoryAdapter(SourceAdapter):
    name = "fpl_history"
    requires = "fpl"
    tier = TIER_DATA
    ttl = 30 * DAY  # finished seasons do not change
    rate, per = 1, 1.0
    base_url = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/"

    def csv(self, season: str, path: str) -> tuple[pd.DataFrame, object]:
        res = self.client.get(f"{self.base_url}{season}/{path}")
        return pd.read_csv(io.BytesIO(res.content), encoding="utf-8", on_bad_lines="skip"), res

    def ingest(self, seasons: list[str] | None = None) -> dict[str, int]:
        counts = {"matches": 0, "match_observations": 0, "player_match_stats": 0}
        for season in seasons or SEASONS_WITH_XG:
            teams, _ = self.csv(season, "teams.csv")
            fixtures, fres = self.csv(season, "fixtures.csv")
            gw, gres = self.csv(season, "gws/merged_gw.csv")
            players, _ = self.csv(season, "players_raw.csv")
            team_slug = self.team_slugs(teams)
            upsert(self.ctx.con, "entity_ids", self.team_ids(teams, team_slug))
            matches, obs = self.parse_matches(season, fixtures, gw, team_slug, fres.fetched_at)
            counts["matches"] += upsert(self.ctx.con, "matches", matches)
            counts["match_observations"] += upsert(self.ctx.con, "match_observations", obs)
            stats = self.parse_player_stats(season, fixtures, gw, players, team_slug, gres.fetched_at)
            counts["player_match_stats"] += upsert(self.ctx.con, "player_match_stats", stats)
        return counts

    def team_slugs(self, teams: pd.DataFrame) -> dict[int, str]:
        by_alias = ClubResolver.from_config()
        out = {}
        for t in teams.itertuples():
            m = self.ctx.reep.lookup("opta", "team_numeric", int(t.code))
            out[int(t.id)] = slugify(m.label) if m else (by_alias.resolve(t.name) or slugify(t.name))
        return out

    def team_ids(self, teams: pd.DataFrame, team_slug: dict[int, str]) -> list[dict]:
        """Crosswalk rows for every club seen in history (incl. relegated sides), so other sources can join."""
        rows = []
        for t in teams.itertuples():
            m = self.ctx.reep.lookup("opta", "team_numeric", int(t.code))
            if not m:
                continue
            slug = team_slug[int(t.id)]
            for key, ext in m.ids.items():
                prov, ns = key.split("/", 1)
                if prov in CLUB_PROVIDERS or key == "opta/team_numeric":
                    rows.append(
                        {
                            "entity_type": "club",
                            "dugout_id": slug,
                            "provider": prov,
                            "namespace": ns,
                            "external_id": str(ext),
                            "reep_id": m.reep_id,
                        }
                    )
        return rows

    def parse_matches(self, season, fixtures, gw, team_slug, fetched_at: datetime):
        gw = gw.assign(was_home=gw["was_home"].astype(str).str.lower().eq("true"))
        xgc = gw.groupby(["fixture", "was_home"])["expected_goals_conceded"].max()
        no_xg = untracked(gw, ["expected_goals", "expected_goals_conceded"])
        matches, obs = [], []
        for f in fixtures[fixtures["finished"].astype(str).str.lower().eq("true")].itertuples():
            home, away = team_slug[int(f.team_h)], team_slug[int(f.team_a)]
            mid = match_id(self.ctx.league, season, home, away)
            ko = pd.Timestamp(f.kickoff_time).to_pydatetime()
            hg, ag = int(f.team_h_score), int(f.team_a_score)
            matches.append(
                {
                    "match_id": mid,
                    "league": self.ctx.league,
                    "season": season,
                    "gameweek": int(f.event),
                    "kickoff_utc": ko,
                    "home_club_id": home,
                    "away_club_id": away,
                    "status": "finished",
                    "home_goals": hg,
                    "away_goals": ag,
                    "updated_at": fetched_at,
                }
            )
            obs.append(
                {
                    "match_id": mid,
                    "source": self.name,
                    "external_id": str(f.id),
                    "status": "finished",
                    "home_goals": hg,
                    "away_goals": ag,
                    # home xG = what the away side conceded, and vice versa
                    "home_xg": None if f.id in no_xg else _num(xgc.get((f.id, False))),
                    "away_xg": None if f.id in no_xg else _num(xgc.get((f.id, True))),
                    "fetched_at": fetched_at,
                }
            )
        return matches, obs

    def parse_player_stats(
        self, season, fixtures, gw, players, team_slug, fetched_at: datetime
    ) -> list[dict]:
        fx = fixtures.set_index("id")
        code_of = dict(zip(players["id"], players["code"], strict=False))
        reep_cache: dict[int, str] = {}

        def pid(element: int) -> str:
            code = int(code_of.get(element, -1))
            if code not in reep_cache:
                m = self.ctx.reep.lookup("opta", "person_numeric", code)
                reep_cache[code] = m.reep_id if m else f"opta:{code}"
            return reep_cache[code]

        no_xg = untracked(gw, ["expected_goals", "expected_goals_conceded"])
        no_starts = untracked(gw, ["starts"]) if "starts" in gw else set(gw["fixture"])
        no_def = untracked(gw, list(DEFENSIVE)) if set(DEFENSIVE) <= set(gw.columns) else set(gw["fixture"])
        rows = []
        played = gw[gw["minutes"] > 0]
        for r in played.itertuples():
            if r.fixture not in fx.index:
                continue
            f = fx.loc[r.fixture]
            home, away = team_slug[int(f.team_h)], team_slug[int(f.team_a)]
            is_home = str(r.was_home).lower() == "true"
            rows.append(
                {
                    "player_id": pid(int(r.element)),
                    "match_id": match_id(self.ctx.league, season, home, away),
                    "source": self.name,
                    "club_id": home if is_home else away,
                    "minutes": int(r.minutes),
                    "goals": int(r.goals_scored),
                    "assists": int(r.assists),
                    "xg": None if r.fixture in no_xg else _num(r.expected_goals),
                    "xa": None if r.fixture in no_xg else _num(r.expected_assists),
                    "starts": None if r.fixture in no_starts else int(r.starts),
                    "saves": _int(getattr(r, "saves", None)),
                    "influence": _num(getattr(r, "influence", None)),
                    "creativity": _num(getattr(r, "creativity", None)),
                    "threat": _num(getattr(r, "threat", None)),
                    "bps": _int(getattr(r, "bps", None)),
                    "xgc": None if r.fixture in no_xg else _num(getattr(r, "expected_goals_conceded", None)),
                    **{
                        dst: None if r.fixture in no_def else _int(getattr(r, src, None))
                        for src, dst in DEFENSIVE.items()
                    },
                    "yellow": int(r.yellow_cards),
                    "red": int(r.red_cards),
                    "fetched_at": fetched_at,
                }
            )
        return rows


def untracked(gw: pd.DataFrame, cols: list[str]) -> set:
    """Fixtures where a stat was not yet collected: every player row is 0. FPL added xG and `starts` partway
    through 2022-23 (xG from gameweek 16); earlier fixtures carry zeros that must read as missing, not as 0."""
    tracked = (gw[cols].fillna(0) != 0).any(axis=1).groupby(gw["fixture"]).any()
    return set(tracked[~tracked].index)


def _int(x) -> int | None:
    v = _num(x)
    return None if v is None else int(v)


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v
