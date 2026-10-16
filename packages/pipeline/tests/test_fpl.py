import gzip
import json

from conftest import FIXTURES
from dugout_verify.jsonpath import extract


def _fixture(url: str):
    idx = json.loads((FIXTURES / "fpl" / "index.json").read_text())
    return json.loads(gzip.decompress((FIXTURES / "fpl" / idx[url]).read_bytes()))


def test_fpl_ingest_builds_clubs_players_and_ids(ctx_with_fpl):
    con = ctx_with_fpl.con
    assert con.execute("SELECT count(*) FROM clubs").fetchone()[0] == 20
    liv = con.execute("SELECT reep_id, name FROM clubs WHERE club_id='liverpool'").fetchone()
    assert liv == ("rt0fcab1a2109ab8", "Liverpool")
    assert con.execute("SELECT count(*) FROM clubs WHERE reep_id IS NULL").fetchone()[0] == 0
    # players are not in the test Reep index: they fall back to an Opta-based id
    assert con.execute("SELECT count(*) FROM players WHERE player_id NOT LIKE 'opta:%'").fetchone()[0] == 0
    # Reep crosswalk ids were copied into entity_ids
    assert con.execute("""SELECT external_id FROM entity_ids WHERE dugout_id='liverpool'
                          AND provider='api_football'""").fetchone() == ("40",)
    assert (
        con.execute("SELECT count(*) FROM players WHERE club_id IN ('liverpool','arsenal')").fetchone()[0] > 0
    )


def test_fpl_injuries_and_matches(ctx_with_fpl):
    con = ctx_with_fpl.con
    statuses = {r[0] for r in con.execute("SELECT status FROM injuries").fetchall()}
    assert statuses <= {"available", "doubtful", "injured", "suspended", "unavailable", "ineligible"}
    assert con.execute("SELECT count(*) FROM injuries").fetchone()[0] >= 1
    finished = con.execute("SELECT match_id, home_goals FROM matches WHERE status='finished'").fetchall()
    assert finished and all(mid.startswith("premier_league:2026-27:") for mid, _ in finished)
    assert con.execute("SELECT count(*) FROM player_match_stats").fetchone()[0] > 0


def test_fpl_ledger_rows_are_reproducible_from_source(ctx_with_fpl):
    """Every ledger value must be re-extractable from the recorded response with its stored query."""
    con = ctx_with_fpl.con
    rows = con.execute("SELECT value_num, query FROM evidence_ledger WHERE source='fpl'").fetchall()
    assert rows
    for value, q in rows:
        q = json.loads(q)
        assert q["kind"] == "http_json"
        raw = extract(_fixture(q["url"]), q["path"])
        assert value == (float(raw) if raw is not None else None)


def test_fpl_ingest_is_idempotent(ctx_with_fpl):
    from dugout_pipeline.adapters.fpl import FPLAdapter

    before = con_counts(ctx_with_fpl.con)
    a = FPLAdapter(ctx_with_fpl)
    a.ingest()
    a.close()
    assert con_counts(ctx_with_fpl.con) == before


def con_counts(con):
    return {
        t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("clubs", "players", "matches", "injuries", "entity_ids", "evidence_ledger")
    }


def test_players_who_joined_another_club_leave_the_squad():
    from dugout_pipeline.adapters.fpl import left_club

    assert left_club({"status": "u", "news": "Has joined Valencia on loan for the rest of the season"})
    assert left_club({"status": "u", "news": "has joined  Kasimpasa on loan for the rest of the seaon."})
    assert not left_club({"status": "u", "news": "Personal reasons"})
    assert not left_club({"status": "i", "news": "Hamstring injury - Expected back 25 Oct"})
    assert not left_club({"status": "a", "news": ""})
