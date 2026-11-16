"""Regression coverage for the offline kickoff and API quota guard."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "kickoff_lineups_due", Path(__file__).resolve().parents[3] / "scripts/kickoff_lineups_due.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
due_clubs = guard.due_clubs

NOW = datetime(2026, 10, 19, 14, tzinfo=UTC)


def row(match: str, kickoff: datetime, as_of: datetime = NOW) -> dict[str, str]:
    return {"match_id": match, "kickoff_utc": kickoff.isoformat(), "as_of": as_of.isoformat()}


def test_filter_deduplicate_and_schedule_window():
    match = "premier_league:2026-27:arsenal:chelsea"
    rows = [
        row(match, NOW + timedelta(minutes=45)),
        row(match, NOW + timedelta(minutes=45)),
        row("premier_league:2026-27:everton:fulham", NOW + timedelta(minutes=61)),
        row("premier_league:2026-27:leeds-united:liverpool", NOW),
        row("serie_a:2026-27:internazionale:milan", NOW + timedelta(minutes=30)),
    ]
    assert due_clubs(rows, NOW) == ["arsenal", "chelsea"]


def test_newer_kickoff_supersedes_stale_forecast_regardless_of_order():
    match = "premier_league:2026-27:arsenal:liverpool"
    rows = [
        row(match, NOW + timedelta(minutes=30), NOW - timedelta(days=1)),
        row(match, NOW + timedelta(days=2), NOW - timedelta(hours=1)),
    ]
    assert due_clubs(rows, NOW) == []
    assert due_clubs(list(reversed(rows)), NOW) == []


def test_bad_ids_and_naive_timestamps_are_ignored():
    rows = [
        row("premier_league:2026-27:arsenal:bad;echo", NOW + timedelta(minutes=40)),
        row("premier_league:2026-27:arsenal", NOW + timedelta(minutes=40)),
        row("premier_league:2026-27:arsenal:chelsea", NOW.replace(tzinfo=None)),
    ]
    assert due_clubs(rows, NOW) == []
