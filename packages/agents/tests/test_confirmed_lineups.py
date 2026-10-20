"""Confirmed provider starters must reach the same verified dossier as predictions."""

from datetime import UTC, datetime, timedelta

import pytest
from dugout_agents import dossier, tools
from dugout_agents.facts import Toolbox
from dugout_agents.graph import Context
from dugout_pipeline.db import connect, upsert
from dugout_verify.factcheck import FactChecker
from dugout_verify.ledger import EvidenceLedger

NOW = datetime(2026, 10, 19, 14, tzinfo=UTC)
MID = "premier_league:2026-27:liverpool:brentford"
NAMES = [
    "Alex Keeper",
    "Ben Back",
    "Carl Back",
    "Dan Back",
    "Evan Back",
    "Finn Mid",
    "Glen Mid",
    "Hugh Mid",
    "Ivan Forward",
    "Jack Forward",
    "Karl Forward",
]
POSITIONS = ["G", *["D"] * 4, *["M"] * 3, *["F"] * 3]


class NoSemanticCalls:
    """Structured, ledger-backed claims need neither an embedding nor a news model."""

    def embed(self, *args, **kwargs):
        raise AssertionError("confirmed warehouse facts must not need semantic inference")

    def scores(self, *args, **kwargs):
        raise AssertionError("confirmed warehouse facts must not need semantic inference")


@pytest.fixture
def ctx():
    con = connect()
    upsert(
        con,
        "clubs",
        [
            {
                "club_id": club,
                "name": club.title(),
                "league": "premier_league",
                "season": "2026-27",
                "updated_at": NOW,
            }
            for club in ("liverpool", "brentford")
        ],
    )
    upsert(
        con,
        "matches",
        [
            {
                "match_id": MID,
                "league": "premier_league",
                "season": "2026-27",
                "home_club_id": "liverpool",
                "away_club_id": "brentford",
                "kickoff_utc": NOW + timedelta(minutes=30),
                "status": "scheduled",
                "updated_at": NOW,
            }
        ],
    )
    upsert(
        con,
        "players",
        [
            {
                "player_id": f"player-{i}",
                "name": name,
                "club_id": "liverpool",
                "position": "GK" if i == 0 else "MID",
                "updated_at": NOW,
            }
            for i, name in enumerate(NAMES)
        ],
    )
    upsert(
        con,
        "match_observations",
        [
            {
                "match_id": MID,
                "source": "api_football",
                "external_id": "12345",
                "status": "scheduled",
                "fetched_at": NOW,
            }
        ],
    )
    upsert(
        con,
        "lineups",
        [
            {
                "match_id": MID,
                "club_id": "liverpool",
                "source": "api_football",
                "player_id": f"player-{i}",
                "starter": True,
                "position": position,
                "formation": "4-3-3",
                "fetched_at": NOW,
            }
            for i, position in enumerate(POSITIONS)
        ],
    )
    upsert(
        con,
        "model_outputs",
        [
            {
                "model": "lineup",
                "version": "test",
                "entity_type": "club",
                "entity_id": "liverpool",
                "metric": "predicted_formation",
                "context": MID,
                "value_text": "4-4-2",
                "as_of": NOW,
            }
        ],
    )
    ledger = EvidenceLedger(con)
    tb = Toolbox(con, ledger, NOW)
    semantics = NoSemanticCalls()
    checker = FactChecker([], semantics, semantics, now=NOW, aliases=tb.aliases)
    yield Context(tb, ledger, checker, None)
    con.close()


def test_confirmed_starters_override_prediction_in_verified_dossier(ctx):
    doc, _ = dossier.build(ctx, "liverpool")
    section = next(s for s in doc["sections"] if s["agent"] == "lineup")
    text = " ".join(
        seg.get("text", seg.get("display", "")) for claim in section["claims"] for seg in claim["segments"]
    )
    assert section["status"] == "published"
    assert section["title"] == "Confirmed XI"
    assert "confirmed formation" in text.lower() and "4-3-3" in text
    assert all(f"Confirmed to start: {name}" in text for name in NAMES)
    assert "4-4-2" not in text and "Predicted to start" not in text
    stats = [doc["stats"][item["stat"]] for item in doc["key_stats"]["lineup"]]
    assert len(stats) == 12
    assert all(s["source"] == "api_football" and s["query_kind"] == "sql" for s in stats)
    assert all(s["source_url"].endswith("fixtures/lineups?fixture=12345") for s in stats)
    assert all(s["fetched_at"] == NOW.isoformat() for s in stats)
    assert doc["verification"]["stat_auditor"]["ok"]


@pytest.mark.parametrize(
    "damage",
    [
        "incomplete",
        "extra",
        "unknown",
        "wrong_club",
        "wrong_match",
        "stale_match",
        "position",
        "goalkeepers",
        "formation",
        "snapshot",
        "future_snapshot",
        "old_snapshot",
        "no_provider_id",
    ],
)
def test_bad_or_unrelated_provider_snapshot_keeps_prediction(ctx, damage):
    con = ctx.toolbox.con
    changes = {
        "incomplete": "DELETE FROM lineups WHERE player_id = 'player-10'",
        "unknown": "UPDATE lineups SET player_id = 'unmatched' WHERE player_id = 'player-10'",
        "wrong_club": "UPDATE lineups SET club_id = 'brentford'",
        "wrong_match": "UPDATE lineups SET match_id = 'other-fixture'",
        "stale_match": "UPDATE matches SET kickoff_utc = kickoff_utc - INTERVAL 1 DAY",
        "position": "UPDATE lineups SET position = '?' WHERE player_id = 'player-10'",
        "goalkeepers": "UPDATE lineups SET position = 'G' WHERE player_id = 'player-10'",
        "formation": "UPDATE lineups SET formation = '4-4-9'",
        "snapshot": "UPDATE lineups SET fetched_at = fetched_at - INTERVAL 1 MINUTE WHERE player_id = 'player-10'",
        "future_snapshot": "UPDATE lineups SET fetched_at = fetched_at + INTERVAL 1 MINUTE",
        "old_snapshot": "UPDATE lineups SET fetched_at = fetched_at - INTERVAL 1 DAY",
        "no_provider_id": "DELETE FROM match_observations",
    }
    if damage == "extra":
        con.execute(
            "INSERT INTO lineups SELECT match_id, club_id, source, formation, 'extra', starter, "
            "position, grid, fetched_at FROM lineups LIMIT 1"
        )
    else:
        con.execute(changes[damage])
    facts = tools.predicted_xi(ctx.toolbox, "liverpool")
    assert facts and all("confirmed" not in f.label for f in facts)
    assert ctx.ledger.get(facts[0].eid).source == "dugout_model:lineup"
