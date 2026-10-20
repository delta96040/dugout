"""API-Football player season stats backfill: club pages, Transfermarkt ids, daily cap, account reserve, resume."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from conftest import make_ctx
from dugout_pipeline.adapters import api_football_stats as afs
from dugout_pipeline.ids import ReepMatch, StaticIndex


@pytest.fixture
def stats_ctx(settings, tmp_path, monkeypatch):
    """Recorded Premier League 2024 pages (hand-trimmed from the cache: Liverpool and Arsenal, six players), a Reep
    bridge for Alisson, a miniature transfermarkt-datasets (Robertson by date of birth, Kelleher by name in the
    club's squad), the Premier League only and one season."""
    tm = tmp_path / "tm"
    tm.mkdir()
    monkeypatch.setattr(afs, "TM_DIR", tm)
    monkeypatch.setattr(afs, "leagues", lambda: ["premier_league"])
    monkeypatch.setattr(afs, "SEASONS", (2024,))
    pd.DataFrame(
        {
            "player_id": [7001, 7002, 7003],
            "name": ["Andrew Robertson", "Caoimhín Kelleher", "Someone Else"],
            "date_of_birth": ["1994-03-11", None, "1994-03-11"],
        }
    ).to_parquet(tm / "players.parquet")
    pd.DataFrame(
        {
            "player_id": [7002],
            "player_club_id": [31],
            "date": ["2024-09-01"],
            "competition_id": ["GB1"],
        }
    ).to_parquet(tm / "appearances.parquet")
    pd.DataFrame({"club_id": [31], "name": ["Liverpool FC"]}).to_parquet(tm / "clubs.parquet")
    reep = StaticIndex(
        {
            ("api_football", "player", "280"): ReepMatch(
                "rp-alisson", "Alisson", {"transfermarkt/spieler": "105470"}
            ),
            ("api_football", "team", "40"): ReepMatch("rp-lfc", "Liverpool", {"transfermarkt/verein": "31"}),
        }
    )
    return make_ctx(settings, reep, "api_football_stats", "api_football_history")


def _budget(a, used: int) -> None:
    a.client.budget.path.parent.mkdir(parents=True, exist_ok=True)
    a.client.budget.path.write_text(json.dumps({a.ctx.now.date().isoformat(): used}))
    a.client.budget._today = a.ctx.now.date


def test_backfill_reads_club_pages_and_maps_players(stats_ctx):
    a = afs.ApiFootballStatsAdapter(stats_ctx)
    a.client.budget._today = stats_ctx.now.date
    counts = a.ingest()
    # 1 teams call + Liverpool's two pages + Arsenal's one page
    assert counts == {
        "player_season_rows": 6,
        "rows_matched": 3,
        "api_calls": 4,
        "teams_done": 2,
        "phases_pending": 0,
    }
    df = stats_ctx.con.execute("SELECT * FROM player_season_stats").df().set_index("player_name")
    assert set(df.team_id) == {40, 42} and set(df.season) == {2024} and set(df.league_id) == {39}
    assert (df.loc["Alisson Becker", "tm_player_id"], df.loc["Alisson Becker", "match_how"]) == (
        "105470",
        "reep",
    )
    # two Transfermarkt players share Robertson's birthday: the name decides
    assert (df.loc["A. Robertson", "tm_player_id"], df.loc["A. Robertson", "match_how"]) == (
        "7001",
        "birth_date",
    )
    assert (df.loc["C. Kelleher", "tm_player_id"], df.loc["C. Kelleher", "match_how"]) == (
        "7002",
        "name_in_club",
    )
    assert pd.isna(df.loc["Kepa", "tm_player_id"])  # in no table we hold: no guess
    assert df.loc["A. Robertson", "minutes"] == 2492 and df.loc["A. Robertson", "position"] == "Defender"
    assert df.loc["A. Robertson", "key_passes"] >= 0
    # a second run takes everything from the cache
    assert a.ingest()["api_calls"] == 0
    a.close()


def test_backfill_keeps_the_account_reserve(stats_ctx):
    a = afs.ApiFootballStatsAdapter(stats_ctx)
    _budget(a, a.daily_limit - afs.STATS_RESERVE)  # only the reserve is left: nothing may be fetched
    counts = a.ingest()
    assert counts["api_calls"] == 0 and counts["phases_pending"] == 1
    assert all("players" not in u and "teams" not in u for u in stats_ctx.transport.requests)
    assert stats_ctx.con.execute("SELECT count(*) FROM player_season_stats").fetchone()[0] == 0
    a.close()


def test_backfill_stops_at_the_daily_cap(stats_ctx, monkeypatch):
    monkeypatch.setattr(afs, "DAILY_CAP", 2)
    a = afs.ApiFootballStatsAdapter(stats_ctx)
    a.client.budget._today = stats_ctx.now.date
    counts = a.ingest()
    assert counts["api_calls"] == 2 and counts["teams_done"] == 0 and counts["phases_pending"] == 1
    # the next day continues where it stopped: the cached pages cost nothing
    monkeypatch.setattr(afs, "DAILY_CAP", 70)
    assert a.ingest()["api_calls"] == 2
    a.close()


def test_parse_keeps_the_club_league_and_minutes_only():
    q = afs.Query(team=40, league=39, season=2024)
    item = {
        "player": {"id": 1, "name": "X", "birth": {"date": "2000-01-01"}},
        "statistics": [
            {"team": {"id": 40}, "league": {"id": 39}, "games": {"minutes": 90, "position": "Midfielder"}},
            {"team": {"id": 40}, "league": {"id": 45}, "games": {"minutes": 90}},  # a cup
            {"team": {"id": 99}, "league": {"id": 39}, "games": {"minutes": 90}},  # another club
        ],
    }
    zero = {
        "player": {"id": 2, "name": "Y"},
        "statistics": [{"team": {"id": 40}, "league": {"id": 39}, "games": {}}],
    }
    rows = afs.parse_players([item, zero], q, "u", pd.Timestamp("2026-10-19", tz="UTC"))
    assert [(r["api_player_id"], r["league_id"], r["minutes"], r["key_passes"]) for r in rows] == [
        (1, 39, 90, None)
    ]
