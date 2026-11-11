"""Kickoff refreshes spend requests only on due Premier League fixture XIs."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import sample
from dugout_pipeline.adapters.api_football import ApiFootballAdapter
from dugout_pipeline.cli import main
from dugout_pipeline.db import upsert
from dugout_pipeline.http import BudgetExceeded, FetchError


@pytest.fixture
def kickoff_ctx(ctx):
    ctx.now = datetime.now(UTC)
    ctx.clubs.add("liverpool", "api_football/team", 40)
    ctx.clubs.add("brentford", "api_football/team", 55)
    for team in json.loads(sample("api_football_lineups.json"))["response"]:
        club = "liverpool" if team["team"]["id"] == 40 else "brentford"
        upsert(
            ctx.con,
            "players",
            [
                {
                    "player_id": f"api_football:{p['player']['id']}",
                    "name": p["player"]["name"],
                    "club_id": club,
                    "updated_at": ctx.now,
                }
                for p in team["startXI"] + team["substitutes"]
            ],
        )
    mid = "premier_league:2026-27:liverpool:brentford"
    upsert(
        ctx.con,
        "matches",
        [
            {
                "match_id": mid,
                "league": "premier_league",
                "season": "2026-27",
                "kickoff_utc": ctx.now + timedelta(minutes=45),
                "home_club_id": "liverpool",
                "away_club_id": "brentford",
                "status": "scheduled",
                "updated_at": ctx.now,
            }
        ],
    )
    upsert(
        ctx.con,
        "match_observations",
        [
            {
                "match_id": mid,
                "source": "api_football",
                "external_id": "99",
                "status": "scheduled",
                "fetched_at": ctx.now,
            }
        ],
    )
    path = ctx.settings.cache_dir / "budget/api_football.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({ctx.now.date().isoformat(): 0}))
    return ctx


def adapter(ctx, payload=None, status=200):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(
            status, json=payload if payload is not None else json.loads(sample("api_football_lineups.json"))
        )

    ctx.transport = httpx.MockTransport(respond)
    return ApiFootballAdapter(ctx), calls


def test_only_due_fixture_lineups_are_fetched_and_reused(kickoff_ctx):
    a, calls = adapter(kickoff_ctx)
    try:
        assert a.refresh_kickoff_lineups() == ["brentford", "liverpool"]
        assert a.refresh_kickoff_lineups() == ["brentford", "liverpool"]
        assert calls == ["https://v3.football.api-sports.io/fixtures/lineups?fixture=99"]
        assert a.client.budget.used() == 1
        assert kickoff_ctx.con.execute("SELECT count(*) FROM lineups WHERE starter").fetchone()[0] == 22
    finally:
        a.close()


def test_reserve_is_kept_before_any_request(kickoff_ctx):
    a, calls = adapter(kickoff_ctx)
    a.client.budget.path.write_text(json.dumps({kickoff_ctx.now.date().isoformat(): 75}))
    try:
        with pytest.raises(BudgetExceeded):
            a.refresh_kickoff_lineups()
        assert calls == []
    finally:
        a.close()


@pytest.mark.parametrize("bad", ["stale", "formation", "club"])
def test_unusable_old_snapshot_is_fetched_again(kickoff_ctx, bad):
    a, calls = adapter(kickoff_ctx)
    mid = "premier_league:2026-27:liverpool:brentford"
    old = a.parse_lineups(mid, json.loads(sample("api_football_lineups.json")), kickoff_ctx.now)
    if bad == "stale":
        for row in old:
            row["fetched_at"] -= timedelta(days=1)
    elif bad == "formation":
        for row in old:
            row["formation"] = "4-4-4"
    else:
        kickoff_ctx.con.execute(
            "UPDATE players SET club_id='arsenal' WHERE player_id=?", [old[0]["player_id"]]
        )
    upsert(kickoff_ctx.con, "lineups", old)
    try:
        if bad == "club":
            with pytest.raises(ValueError, match="incomplete"):
                a.refresh_kickoff_lineups()
        else:
            assert a.refresh_kickoff_lineups() == ["brentford", "liverpool"]
        assert len(calls) == 1
    finally:
        a.close()


@pytest.mark.parametrize("contents", [None, "{broken", "[]", '{"bad":1}'])
def test_missing_or_corrupt_shared_counter_does_not_spend(kickoff_ctx, contents):
    a, calls = adapter(kickoff_ctx)
    if contents is None:
        a.client.budget.path.unlink()
    else:
        a.client.budget.path.write_text(contents)
    try:
        with pytest.raises((BudgetExceeded, ValueError)):
            a.refresh_kickoff_lineups()
        assert calls == []
    finally:
        a.close()


def test_transient_error_spends_one_actual_request_and_is_not_retried(kickoff_ctx):
    a, calls = adapter(kickoff_ctx, {}, 503)
    try:
        with pytest.raises(FetchError):
            a.refresh_kickoff_lineups()
        assert len(calls) == a.client.budget.used() == 1
    finally:
        a.close()


def test_empty_response_does_not_publish_or_erase_rows(kickoff_ctx):
    a, _ = adapter(kickoff_ctx, {"response": [], "errors": []})
    try:
        assert a.refresh_kickoff_lineups() == []
        assert kickoff_ctx.con.execute("SELECT count(*) FROM lineups").fetchone()[0] == 0
    finally:
        a.close()


def test_replacement_removes_previous_dropped_starter(kickoff_ctx):
    a, _ = adapter(kickoff_ctx)
    old = a.parse_lineups(
        "premier_league:2026-27:liverpool:brentford",
        json.loads(sample("api_football_lineups.json")),
        kickoff_ctx.now,
    )
    old = [r for r in old if r["starter"]]
    old[0]["player_id"] = "dropped-player"
    old[0]["position"] = None  # an invalid old snapshot must be replaced rather than reused
    upsert(kickoff_ctx.con, "lineups", old)
    try:
        assert a.refresh_kickoff_lineups() == ["brentford", "liverpool"]
        assert (
            kickoff_ctx.con.execute(
                "SELECT count(*) FROM lineups WHERE player_id='dropped-player'"
            ).fetchone()[0]
            == 0
        )
        assert kickoff_ctx.con.execute("SELECT count(*) FROM lineups WHERE starter").fetchone()[0] == 22
    finally:
        a.close()


def test_incomplete_response_preserves_old_snapshot_and_fails(kickoff_ctx):
    payload = json.loads(sample("api_football_lineups.json"))
    payload["response"][0]["startXI"].pop()
    a, _ = adapter(kickoff_ctx, payload)
    try:
        with pytest.raises(ValueError, match="incomplete"):
            a.refresh_kickoff_lineups()
        assert kickoff_ctx.con.execute("SELECT count(*) FROM lineups").fetchone()[0] == 0
    finally:
        a.close()


def test_cli_no_due_fixture_needs_no_credential_or_network(ctx, monkeypatch, capsys):
    ctx.settings.api_football_key = None
    monkeypatch.setattr("dugout_pipeline.cli._ctx", lambda _: ctx)
    assert main(["kickoff-lineups"]) == 0
    assert "confirmed=false" in capsys.readouterr().out


def test_cli_provider_error_fails_closed(kickoff_ctx, monkeypatch):
    kickoff_ctx.transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"errors": {"plan": "unavailable"}})
    )
    monkeypatch.setattr("dugout_pipeline.cli._ctx", lambda _: kickoff_ctx)
    assert main(["kickoff-lineups"]) == 1
