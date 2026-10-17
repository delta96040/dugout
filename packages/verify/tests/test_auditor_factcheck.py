from datetime import UTC, datetime, timedelta

import duckdb
import numpy as np
import pytest
from dugout_verify.auditor import audit, stray_numbers
from dugout_verify.claims import claims, classify, split_sentences
from dugout_verify.factcheck import FactChecker, Passage, near_duplicate
from dugout_verify.ledger import EvidenceLedger, render

T = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE goals (player VARCHAR, n INTEGER)")
    c.execute("INSERT INTO goals VALUES ('salah', 7)")
    c.execute(
        """CREATE TABLE match_observations (match_id VARCHAR, source VARCHAR, home_goals INTEGER,
           away_goals INTEGER)"""
    )
    c.execute("INSERT INTO match_observations VALUES ('m1', 'fpl', 2, 1), ('m1', 'football_data_org', 2, 0)")
    return c


@pytest.fixture
def ledger(con):
    return EvidenceLedger(con)


def _goals(ledger):
    eid, v = ledger.record_sql(
        "SELECT n FROM goals WHERE player = ?",
        ["salah"],
        entity_type="player",
        entity_id="salah",
        metric="season_goals_scored",
        source="fpl",
        source_tier=1,
        fetched_at=T,
    )
    assert v == 7.0
    return eid


def test_stray_numbers_catch_digits_and_words_but_not_placeholders():
    t = "He has {{stat:ev_0123456789abcdef}} goals, twelve shots and 3 assists in 2026."
    assert stray_numbers(t) == ["twelve", "3", "2", "0", "2", "6"]
    assert stray_numbers("One of the best, {{stat:ev_0123456789abcdef|.1f}} per game.") == []
    assert stray_numbers("Mainz 05 away.", allowed_names=["Mainz 05"]) == []


def test_audit_passes_clean_draft_and_reruns_sql(ledger, con):
    eid = _goals(ledger)
    rep = audit(f"Salah has scored {{{{stat:{eid}}}}} goals.", ledger, con, cross_checks={})
    assert rep.ok and rep.numbers_untraceable == 0 and rep.stat_status[eid] == "single_source"


def test_audit_fails_when_warehouse_changed(ledger, con):
    eid = _goals(ledger)
    con.execute("UPDATE goals SET n = 8")
    rep = audit(f"Salah has {{{{stat:{eid}}}}} goals.", ledger, con, cross_checks={})
    assert not rep.ok and rep.errors[0].check == "rerun"


def test_audit_fails_on_stray_number_and_bad_placeholder(ledger, con):
    rep = audit("He scored 7 goals {{stat:ev_nothex}}.", ledger, con)
    checks = {f.check for f in rep.errors}
    assert checks == {"stray_number", "placeholder"} and rep.numbers_untraceable >= 1


def test_cross_source_goal_disagreement_is_an_error(ledger, con):
    eid = ledger.record_sql(
        "SELECT away_goals FROM match_observations WHERE match_id = ? AND source = 'fpl'",
        ["m1"],
        entity_type="match",
        entity_id="m1",
        metric="away_goals",
        source="fpl",
        source_tier=1,
        fetched_at=T,
    )[0]
    rep = audit(f"They lost by {{{{stat:{eid}}}}}.", ledger, con)
    assert not rep.ok and rep.stat_status[eid] == "conflicting"


def test_render_formats(ledger, con):
    eid = _goals(ledger)
    assert render(f"{{{{stat:{eid}|ord}}}}", ledger)[0] == "7th"


def test_sentence_split_keeps_placeholders_and_abbreviations():
    t = "Liverpool vs. Arsenal is on Sunday. Salah has {{stat:ev_0123456789abcdef|.1f}} xG. They should rotate."
    s = split_sentences(t)
    assert len(s) == 3 and s[1].endswith("xG.")
    assert [c.type for c in claims(t)] == ["fact", "stat", "analysis"]
    assert classify("Isak could miss the game with a thigh injury.") == "injury"
    assert classify("Liverpool have agreed a fee with Napoli.") == "transfer"
    club = "Dream target: Jan Schöppner (1. Fußballclub Heidenheim 1846), who is 25. He plays for Mainz 05. Next."
    assert len(split_sentences(club)) == 3 and split_sentences(club)[1] == "He plays for Mainz 05."


class FakeEmbedder:
    def embed(self, texts, query=False):
        v = np.array([[len(t) % 7 + 1.0, 1.0] for t in texts])
        return v / np.linalg.norm(v, axis=1, keepdims=True)


class KeywordNLI:
    """Entails when the premise contains the hypothesis' key word; contradicts on 'fit'/'returned'."""

    def __init__(self, key):
        self.key = key

    def scores(self, premises, hypothesis):
        out = []
        for p in premises:
            if "returned to training" in p:
                out.append([0.05, 0.05, 0.9])
            elif self.key in p:
                out.append([0.9, 0.05, 0.05])
            else:
                out.append([0.1, 0.8, 0.1])
        return np.array(out)


NOW = datetime(2026, 10, 15, tzinfo=UTC)


def _p(text, outlet, tier=2, days=1, official=False):
    return Passage(text, outlet, tier, published_at=NOW - timedelta(days=days), official=official)


def test_two_independent_sources_verify_one_does_not():
    passages = [
        _p("Isak is out with a thigh injury, the club said.", "BBC Sport"),
        _p("Isak has a thigh injury and misses the derby.", "The Guardian"),
        _p("Isak thigh injury: Observer copy.", "The Observer"),
    ]
    fc = FactChecker(passages, KeywordNLI("thigh"), FakeEmbedder(), now=NOW)
    r = fc.check_news(0, "Isak has a thigh injury.", "injury")
    assert r.status == "verified" and len(r.support) == 2  # Guardian + Observer count once
    fc = FactChecker(passages[1:], KeywordNLI("thigh"), FakeEmbedder(), now=NOW)
    assert fc.check_news(0, "Isak has a thigh injury.", "injury").status == "unverified"


def test_official_source_alone_verifies_and_stale_news_is_ignored():
    passages = [
        _p("Isak: thigh injury, unknown return date (Fantasy Premier League).", "FPL", tier=1, official=True),
        _p("Isak thigh injury old story.", "BBC Sport", days=40),
    ]
    fc = FactChecker(passages, KeywordNLI("thigh"), FakeEmbedder(), now=NOW)
    r = fc.check_news(0, "Isak has a thigh injury.", "injury")
    assert r.status == "verified" and [s["outlet"] for s in r.support] == ["FPL"]


def test_contradiction_from_major_outlet_is_conflicting():
    passages = [
        _p("Isak thigh injury.", "The Guardian"),
        _p("Isak thigh injury.", "ESPN"),
        _p("Isak has returned to training.", "BBC Sport"),
    ]
    fc = FactChecker(passages, KeywordNLI("thigh"), FakeEmbedder(), now=NOW)
    assert fc.check_news(0, "Isak has a thigh injury.", "injury").status == "conflicting"


def test_stat_binding(ledger):
    eid = _goals(ledger)
    fc = FactChecker(
        [], KeywordNLI("x"), FakeEmbedder(), now=NOW, aliases={"salah": ["Mohamed Salah", "Salah"]}
    )
    ok = fc.check(f"Salah has scored {{{{stat:{eid}}}}} goals.", ledger)
    assert ok.ok and ok.claims[0].status == "verified"
    wrong_player = fc.check(f"Gakpo has scored {{{{stat:{eid}}}}} goals.", ledger)
    assert not wrong_player.ok
    wrong_metric = fc.check(f"Salah has made {{{{stat:{eid}}}}} tackles.", ledger)
    assert not wrong_metric.ok


def test_near_duplicate():
    a = "Liverpool forward Alexander Isak will miss the next three weeks with a thigh problem, the club said"
    assert near_duplicate(a, a + " on Friday.")
    assert not near_duplicate(
        a, "Arsenal beat Lille in the Champions League on Tuesday night at the Emirates"
    )


def test_binding_requires_number_next_to_its_own_name(ledger, con):
    con.execute("INSERT INTO goals VALUES ('gakpo', 3)")
    s = _goals(ledger)
    g = ledger.record_sql(
        "SELECT n FROM goals WHERE player = ?",
        ["gakpo"],
        entity_type="player",
        entity_id="gakpo",
        metric="season_goals_scored",
        source="fpl",
        source_tier=1,
        fetched_at=T,
    )[0]
    aliases = {"salah": ["Salah"], "gakpo": ["Gakpo"]}
    fc = FactChecker([], KeywordNLI("x"), FakeEmbedder(), now=NOW, aliases=aliases)
    ok = fc.check(f"Salah has {{{{stat:{s}}}}} goals and Gakpo has {{{{stat:{g}}}}} goals.", ledger)
    assert ok.ok
    swapped = fc.check(f"Salah has {{{{stat:{g}}}}} goals and Gakpo has {{{{stat:{s}}}}} goals.", ledger)
    assert not swapped.ok and "next to another name" in swapped.claims[0].reason
    far = fc.check(f"Salah, who has {{{{stat:{s}}}}}, is quick and scores goals.", ledger)
    assert not far.ok and "near its number" in far.claims[0].reason


def test_stat_sentences_about_models_are_not_news_claims():
    from dugout_verify.claims import secondary_type
    from dugout_verify.factcheck import METRIC_WORDS

    assert classify("Transfermarkt is a website.") == "fact"
    s = "Transfermarkt values Bukayo Saka at {{stat:ev_0123456789abcdef|eur_m}}."
    assert secondary_type(s, [METRIC_WORDS["market_value"]]) is None
    s = "Ezri Konsa is in the {{stat:ev_0123456789abcdef}} risk tier of the injury-risk model."
    assert secondary_type(s, [METRIC_WORDS["injury_risk_tier"]]) is None
    s = "The realism model gives a move for Adam Wharton to Arsenal a {{stat:ev_0123456789abcdef|pct1}} chance in a transfer window."
    assert secondary_type(s, [METRIC_WORDS["realism"]]) is None
    assert (
        secondary_type("Isak, out with a thigh injury, has {{stat:ev_0123456789abcdef}} goals.", [])
        == "injury"
    )
