from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from dugout_agents import dossier, writer
from dugout_agents.facts import Toolbox
from dugout_agents.graph import Context, run
from dugout_agents.llm import ScriptedLLM
from dugout_agents.runtime import passages
from dugout_agents.specialists import route
from dugout_pipeline.db import connect
from dugout_verify.auditor import stray_numbers
from dugout_verify.factcheck import FactChecker
from dugout_verify.ledger import EvidenceLedger

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
MID = "premier_league:2026-27:brentford:liverpool"


@pytest.fixture
def con():
    c = connect(":memory:")
    c.execute(
        """INSERT INTO clubs VALUES ('liverpool', NULL, 'Liverpool', 'LIV', 'premier_league', '2026-27', 'Anfield',
           NULL, NULL, NULL, ?), ('brentford', NULL, 'Brentford', 'BRE', 'premier_league', '2026-27', NULL, NULL,
           NULL, NULL, ?)""",
        [NOW, NOW],
    )
    c.execute(
        "INSERT INTO players VALUES ('rp1', NULL, 'Alexander Isak', 'Isak', NULL, NULL, 'FWD', 'liverpool', ?)",
        [NOW],
    )
    c.execute(
        "INSERT INTO players VALUES ('rp2', NULL, 'Dominik Szoboszlai', 'Szoboszlai', NULL, NULL, 'MID', "
        "'liverpool', ?)",
        [NOW],
    )
    c.execute(
        "INSERT INTO managers VALUES ('wikidata:Q1', NULL, 'Andoni Iraola', NULL, NULL, 'liverpool', "
        "DATE '2026-06-04', 'wikidata', ?)",
        [NOW],
    )
    c.execute(
        """INSERT INTO standings VALUES ('premier_league', '2026-27', 'liverpool', 'football_data_org', 3, 6, 4,
                 1, 1, 12, 6, 13, 'W,W,D,L,W', ?)""",
        [NOW],
    )
    c.execute(
        """INSERT INTO matches VALUES (?, 'premier_league', '2026-27', 7, ?, 'brentford', 'liverpool',
                 'scheduled', NULL, NULL, ?)""",
        [MID, NOW + timedelta(days=2), NOW],
    )
    for metric, v in (
        ("p_home_win", 0.35),
        ("p_draw", 0.26),
        ("p_away_win", 0.39),
        ("exp_home_goals", 1.5),
        ("exp_away_goals", 1.6),
    ):
        c.execute(
            "INSERT INTO model_outputs VALUES ('predictor', 'p', 'match', ?, ?, '', ?, NULL, ?)",
            [MID, metric, v, NOW],
        )
    c.execute(
        "INSERT INTO model_outputs VALUES ('predictor', 'p', 'match', ?, 'kickoff_utc', '', NULL, ?, ?)",
        [MID, (NOW + timedelta(days=2)).isoformat(), NOW],
    )
    for club, elo in (("liverpool", 1600.7), ("brentford", 1480.2)):
        c.execute(
            "INSERT INTO model_outputs VALUES ('predictor', 'p', 'club', ?, 'elo', '', ?, NULL, ?)",
            [club, elo, NOW],
        )
    c.execute(
        """INSERT INTO injuries VALUES ('rp1', 'liverpool', 'fpl', DATE '2026-10-15', 'injured', 'Thigh injury',
                 0, 'Thigh injury - Unknown return date', ?, NULL, ?)""",
        [NOW, NOW],
    )
    c.execute("CREATE TABLE goals_src (player VARCHAR, goals INTEGER)")
    c.execute("INSERT INTO goals_src VALUES ('rp1', 4)")
    return c


@pytest.fixture
def ledger(con):
    led = EvidenceLedger(con)
    led.record_sql(
        "SELECT goals FROM goals_src WHERE player = ?",
        ["rp1"],
        entity_type="player",
        entity_id="rp1",
        metric="season_goals_scored",
        source="fpl",
        source_tier=1,
        fetched_at=NOW,
    )
    for metric, col in (("league_position", "position"), ("league_points", "points")):
        led.record_sql(
            f"SELECT {col} FROM standings WHERE club_id = ?",
            ["liverpool"],
            entity_type="club",
            entity_id="liverpool",
            metric=metric,
            source="football_data_org",
            source_tier=2,
            fetched_at=NOW,
        )
    return led


class FakeEmbedder:
    def embed(self, texts, query=False):
        return np.ones((len(texts), 2)) / np.sqrt(2)


class LexicalNLI:
    """Entailment when every capitalised word and the injury word of the claim appear in the premise."""

    def scores(self, premises, hypothesis):
        keys = [w.strip(".,").lower() for w in hypothesis.split() if w[:1].isupper() or w.startswith("thigh")]
        return np.array(
            [[0.95, 0.03, 0.02] if all(k in p.lower() for k in keys) else [0.05, 0.9, 0.05] for p in premises]
        )


def make_ctx(con, ledger, llm=None):
    tb = Toolbox(con, ledger, NOW)
    checker = FactChecker(passages(con, tb, NOW), LexicalNLI(), FakeEmbedder(), now=NOW, aliases=tb.aliases)
    return Context(tb, ledger, checker, llm)


def test_routing():
    assert route("How many goals has Isak scored?") == ["club"]
    assert route("Who should we sign to replace him?") == ["scout"]
    assert set(route("Is Isak fit for the next match?")) >= {"news", "match"}
    assert route("Tell me something") == ["club", "news"]


def test_llm_codes_are_substituted_and_numbers_rejected(con, ledger):
    ctx = make_ctx(con, ledger)
    from dugout_agents import tools

    facts = tools.club_profile(ctx.toolbox, "liverpool")
    pos = next(f for f in facts if f.label == "league position")
    pts = next(f for f in facts if f.label == "league points")
    good = f"Liverpool are [{pos.key}] in the Premier League table. Liverpool have [{pts.key}] points."
    llm = ScriptedLLM(["Liverpool are 3rd with 13 points.", good])
    d = writer.write(
        llm,
        role="analyst",
        club="Liverpool",
        question="q",
        facts=facts,
        ledger=ledger,
        checker=ctx.checker,
        allowed_names=[],
    )
    assert d.writer == "llm:scripted" and pos.placeholder in d.template
    assert "numbers written directly" in d.attempts[0]["errors"][0]
    # two bad drafts -> deterministic template
    llm = ScriptedLLM(["They have 13 points.", "Still 13."])
    d = writer.write(
        llm,
        role="analyst",
        club="Liverpool",
        question="q",
        facts=facts,
        ledger=ledger,
        checker=ctx.checker,
        allowed_names=[],
    )
    assert d.writer == "template" and not stray_numbers(d.template)


def test_llm_binding_wrong_subject_rejected(con, ledger):
    ctx = make_ctx(con, ledger)
    from dugout_agents import tools

    facts = tools.player_season(ctx.toolbox, "rp1")
    g = facts[0]
    llm = ScriptedLLM([f"Szoboszlai has scored [{g.key}] goals.", f"Isak has scored [{g.key}] league goals."])
    d = writer.write(
        llm,
        role="analyst",
        club="Liverpool",
        question="q",
        facts=facts,
        ledger=ledger,
        checker=ctx.checker,
        allowed_names=[],
    )
    assert d.writer == "llm:scripted" and "Isak" in d.template
    assert "not named" in d.attempts[0]["errors"][0]


def test_graph_end_to_end_publishes_only_verified(con, ledger):
    ctx = make_ctx(con, ledger)  # no LLM: templates
    state = run(ctx, "How many goals has Alexander Isak scored, and is he fit?", "liverpool")
    assert set(state["agents"]) >= {"club", "news"} and state["focus"] == ["rp1"]
    by = {s.agent: s for s in state["sections"]}
    assert by["club"].status == "published"
    assert "Alexander Isak has scored 4 league goals this season." in by["club"].text
    assert by["news"].status == "published" and "Isak is injured" in by["news"].text
    for s in [*state["sections"], state["summary"]]:
        if s.status == "published":
            assert s.audit["ok"] and s.audit["numbers_untraceable"] == 0
            assert not stray_numbers(s.published_template, ctx.allowed_names())


def test_unverified_sentences_are_cut(con, ledger):
    ctx = make_ctx(con, ledger)
    from dugout_agents.graph import Section, gate

    eid = ctx.toolbox.latest("club", "liverpool", "league_position").id
    sec = Section(
        "club",
        "Club",
        "q",
        template=f"Liverpool are {{{{stat:{eid}|ord}}}} in the table. "
        "Liverpool have agreed a fee with Napoli.",
    )
    gate(sec, ctx)
    assert sec.status == "published" and sec.text == "Liverpool are 3rd in the table."
    assert sec.withheld[0]["status"] == "unverified"


def test_dossier_json(con, ledger):
    ctx = make_ctx(con, ledger)
    doc, _ = dossier.build(ctx, "liverpool")
    assert doc["schema"] == "dugout.club/1" and doc["verification"]["stat_auditor"]["ok"]
    match = next(s for s in doc["sections"] if s["agent"] == "match")
    assert match["status"] == "published"
    stat_ids = [seg["stat"] for c in match["claims"] for seg in c["segments"] if "stat" in seg]
    assert stat_ids and all(doc["stats"][i]["source"].startswith("dugout_model") for i in stat_ids)
    for e in doc["stats"].values():
        assert e["fetched_at"] and e["status"] in ("verified", "single_source", "model_output")


def test_potential_and_fit_for_a_named_player(con, ledger):
    rows = [
        ("lookup", "name", "", None, "Alexander Isak"),
        ("lookup", "club_name", "", None, "Liverpool FC"),
        ("lookup", "age", "", 27.1, None),
        ("lookup", "market_value", "", 90e6, None),
        ("lookup", "level_now", "", None, "among the most valuable players in the Premier League"),
        ("potential", "proj_value_2y", "", 80e6, None),
        ("potential", "proj_value_4y", "", 60e6, None),
        ("potential", "ceiling_value_4y", "", 95e6, None),
        ("potential", "floor_value_4y", "", 30e6, None),
        (
            "potential",
            "level_4y",
            "",
            None,
            "the level of a first-choice player at a top Premier League club",
        ),
        ("potential", "level_ceiling", "", None, "among the most valuable players in the Premier League"),
        ("fit", "position_group", "liverpool", None, "centre-forward"),
        ("fit", "expected_minutes_share", "liverpool", 0.61, None),
        ("fit", "rank_at_position", "liverpool", 1, None),
        ("fit", "fills_need", "liverpool", None, "no"),
    ]
    for model, metric, ctx_, num, text in rows:
        con.execute(
            "INSERT INTO model_outputs VALUES (?, 'v', 'player', 'rp1', ?, ?, ?, ?, ?)",
            [model, metric, ctx_, num, text, NOW],
        )
    ctx = make_ctx(con, ledger)
    state = run(ctx, "How good can Isak become, and how would he fit in at Liverpool?", "liverpool")
    assert set(state["agents"]) == {"potential", "fit"}
    by = {s.agent: s for s in state["sections"]}
    assert by["potential"].status == "published", by["potential"].withheld
    assert "€60.0m by 15 October 2030" in by["potential"].text
    assert by["fit"].status == "published" and "61% of Liverpool's minutes" in by["fit"].text


def test_evasive_llm_note_is_rejected(con, ledger):
    ctx = make_ctx(con, ledger)
    from dugout_agents import tools

    facts = tools.club_profile(ctx.toolbox, "liverpool")
    llm = ScriptedLLM(["We recommend checking the club website.", "We recommend reviewing the data."])
    d = writer.write(
        llm,
        role="analyst",
        club="Liverpool",
        question="q",
        facts=facts,
        ledger=ledger,
        checker=ctx.checker,
        allowed_names=[],
    )
    assert d.writer == "template" and "too few of the facts" in d.attempts[0]["errors"][-1]


def test_rank_gloss_uses_league_places_or_plain_words():
    from dugout_agents.facts import rank_gloss

    pl = {"teams": 20, "champions_league": 4, "europe": 7, "relegation": 3}
    assert rank_gloss(4, 20, pl) == "Champions League places"
    assert rank_gloss(18, 20, pl) == "relegation zone" and rank_gloss(17, 20, pl) == "mid-table"
    # a league without checked places: no claim about Europe or relegation
    assert rank_gloss(2, 18) == "top three" and rank_gloss(16, 18) == "bottom three"
    assert rank_gloss(9, 18) == "top half" and rank_gloss(10, 18) == "bottom half"


def test_dossier_rotation_puts_default_league_first_then_oldest(tmp_path, monkeypatch):
    import json

    from dugout_agents import cli

    monkeypatch.setattr(cli, "CLUBS_DIR", tmp_path)
    (tmp_path / "ajax.json").write_text(json.dumps({"generated_at": "2026-10-16T02:00:00+00:00"}))
    (tmp_path / "psv.json").write_text(json.dumps({"generated_at": "2026-10-15T02:00:00+00:00"}))
    rows = [
        ("ajax", "eredivisie"),
        ("psv", "eredivisie"),
        ("az", "eredivisie"),
        ("arsenal", "premier_league"),
    ]
    assert cli.rotation(rows, "premier_league") == ["arsenal", "az", "psv", "ajax"]


def test_register_adds_a_second_spelling_and_suffix_names_match():
    import re

    from dugout_agents.facts import _person_aliases

    tb = Toolbox.__new__(Toolbox)
    tb.names, tb.aliases = {"p": "Rassoul Ndiaye"}, {"p": _person_aliases("Rassoul Ndiaye", None)}
    shared = tb.aliases["p"]
    tb.register("p", "Rassoul N'Diaye")
    assert tb.aliases["p"] is shared and "Rassoul N'Diaye" in shared and "Rassoul Ndiaye" in shared
    al = _person_aliases("Ronald Koeman Jr.", None)
    assert "Koeman" in al and any(re.search(rf"\b{re.escape(a)}\b", "Ronald Koeman Jr.'s chance") for a in al)


def test_rumour_track_record_follows_its_rumour(con, ledger):
    from dugout_agents import tools
    from dugout_agents.graph import Section, gate

    url = "https://www.theguardian.com/football/2026/oct/10/rumours"
    for nid, player, body in (
        ("n1-p0", "Bob Smith", "Liverpool want Bob Smith."),
        ("n1-p1", "Carl Jones", "Arsenal want Carl Jones."),  # tagged with Liverpool, but does not say so
    ):
        con.execute(
            "INSERT INTO news_items VALUES (?, 'guardian_rumours', 'The Guardian', ?, ?, ?, NULL, ?, ['liverpool'], 5, ?)",
            [nid, url, player, NOW - timedelta(days=5), body, NOW],
        )
        key = player.lower()
        con.execute(
            "INSERT INTO model_outputs VALUES ('rumours', 'r', 'rumour', ?, 'one_in', 'liverpool', 5, NULL, ?)",
            [f"{nid}:{key}", NOW],
        )
    ctx = make_ctx(con, ledger)
    facts = tools.rumours(ctx.toolbox, "liverpool", "in")
    assert [f.label for f in facts].count("rumour track record") == 2
    sec = Section("scout", "Scout", "q", template=" ".join(f.sentence() for f in facts))
    gate(sec, ctx)
    assert sec.status == "published"
    assert "Liverpool have been linked with Bob Smith." in sec.text
    assert (
        "Past reports like this one about Bob Smith came true within half a year about one time in 5."
        in sec.text
    )
    assert "Carl Jones" not in sec.text
    assert any(w["reason"] == "the rumour it is about was not confirmed" for w in sec.withheld)
