import json

from dugout_agents import finetune, writer
from dugout_agents.llm import LLMError, ScriptedLLM
from test_agents import con, ledger, make_ctx  # noqa: F401  (fixtures)


def _prompt(ctx, kind="club"):
    spec = finetune.Spec("liverpool", "premier_league", kind, "How are Liverpool doing this season?")
    return finetune.build(ctx, spec)


def _codes(prompt):
    by = {f.label: f.key for f in prompt.facts}
    return by["league position"], by["league points"]


def test_split_is_stable_per_league_and_disjoint():
    clubs = {f"c{i}": "a" if i < 10 else "b" for i in range(20)}
    s = finetune.split_clubs(clubs)
    assert s == finetune.split_clubs(dict(reversed(clubs.items())))  # order does not matter
    for league in "ab":
        got = [s[c] for c, lg in clubs.items() if lg == league]
        assert got.count("test") == 2 and got.count("val") == 1 and got.count("train") == 7


def test_specs_cover_kinds_and_exclude_non_prose(con, ledger):  # noqa: F811
    ctx = make_ctx(con, ledger)
    specs = finetune.specs(ctx, {"liverpool": "premier_league"}, players_per_club=2)
    kinds = {s.kind for s in specs}
    assert "lineup" not in kinds  # never written by the LLM in production
    assert {"club", "director", "player_club", "potential"} <= kinds
    assert len({s.id for s in specs}) == len(specs)
    assert [s.id for s in specs] == [s.id for s in finetune.specs(ctx, {"liverpool": "premier_league"}, 2)]


def test_prompt_is_the_production_prompt(con, ledger):  # noqa: F811
    ctx = make_ctx(con, ledger)
    p = _prompt(ctx)
    system, user = (m["content"] for m in p.messages())
    assert system == writer.SYSTEM.format(role="club and tactics analyst", club="Liverpool", n=4)
    assert user.startswith("Question: How are Liverpool doing this season?") and "[F" in user


def test_accepted_target_keeps_codes_and_first_prompt(con, ledger):  # noqa: F811
    ctx = make_ctx(con, ledger)
    p = _prompt(ctx)
    pos, pts = _codes(p)
    good = f"Liverpool are [{pos}] in the Premier League table. Liverpool have [{pts}] league points."
    llm = ScriptedLLM(["Liverpool are 3rd with 13 points.", good])
    out = finetune.teach(ctx, llm, p, "scripted")
    assert out.status == "accepted" and out.calls == 2
    ex = out.example
    assert ex["messages"][2]["content"] == good and not ex["first_try"]
    assert "rejected" not in ex["messages"][1]["content"]  # paired with the first prompt, not the retry
    assert ex["teacher"] == "scripted"
    assert not finetune.has_digits(good) and finetune.has_digits("Liverpool have 13 points [F2].")


def test_rejected_and_error_outcomes(con, ledger):  # noqa: F811
    ctx = make_ctx(con, ledger)
    p = _prompt(ctx)
    assert finetune.teach(ctx, ScriptedLLM(["13 points.", "Still 13."]), p, "s").status == "rejected"

    class Down:
        name = "down"

        def complete(self, system, user):
            raise LLMError("rate-limited")

    assert finetune.teach(ctx, Down(), p, "s").status == "error"
    empty = finetune.Prompt(p.spec, p.role, p.club_name, [])
    assert finetune.teach(ctx, Down(), empty, "s").status == "no_facts"


def test_generate_resumes_and_finalise_dedupes(con, ledger, tmp_path):  # noqa: F811
    ctx = make_ctx(con, ledger)

    def reply(system, user):  # codes differ per prompt; answer with the ones in the prompt
        import re

        keys = dict(re.findall(r"- \[(F\d+)\] [^\n]* — (league position|league points)", user))
        inv = {v: k for k, v in keys.items()}
        if len(inv) < 2:
            return "No facts."
        return f"Liverpool are [{inv['league position']}] in the table. Liverpool have [{inv['league points']}] league points."

    llm = ScriptedLLM(reply)
    s1 = finetune.generate(
        ctx, llm, "scripted", tmp_path, max_calls=50, players_per_club=1, test_per_league=0
    )
    assert s1["accepted"] >= 1
    calls = len(llm.calls)
    s2 = finetune.generate(
        ctx, llm, "scripted", tmp_path, max_calls=50, players_per_club=1, test_per_league=0
    )
    assert sum(s2.values()) == 0 and len(llm.calls) == calls  # everything done: nothing re-asked
    # a duplicate note (same text up to fact codes) is dropped
    rows = (tmp_path / "examples.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "examples.jsonl").write_text("\n".join(rows + rows[:1]) + "\n", encoding="utf-8")
    stats = finetune.finalise(tmp_path)
    assert stats["duplicates_dropped"] >= 1
    split = json.loads((tmp_path / "split.json").read_text(encoding="utf-8"))
    out = [json.loads(x) for x in (tmp_path / f"{split['liverpool']}.jsonl").read_text("utf-8").splitlines()]
    assert out and all(r["messages"][2]["role"] == "assistant" for r in out)


def test_offline_teacher_round_trip(con, ledger, tmp_path):  # noqa: F811
    import re

    ctx = make_ctx(con, ledger)
    finetune.generate(
        ctx, ScriptedLLM([]), "s", tmp_path, max_calls=0, test_per_league=0
    )  # writes split.json
    n = finetune.export_prompts(ctx, tmp_path, players_per_club=1)
    prompts = [json.loads(x) for x in (tmp_path / "prompts.jsonl").read_text("utf-8").splitlines()]
    assert n == len(prompts) > 0
    replies = []
    for p in prompts:
        user = p["messages"][1]["content"]
        keys = dict(re.findall(r"- \[(F\d+)\] [^\n]* — (league position|league points)", user))
        inv = {v: k for k, v in keys.items()}
        good = (
            f"<think>scratch</think>Liverpool are [{inv.get('league position')}] in the table. "
            f"Liverpool have [{inv.get('league points')}] league points."
        )
        replies += [
            {
                "id": p["id"],
                "sample": 0,
                "content": "Liverpool are 3rd with 13 points.",
                "messages": p["messages"],
            },
            {"id": p["id"], "sample": 1, "content": good, "messages": p["messages"]},
        ]
    (tmp_path / "replies.jsonl").write_text("".join(json.dumps(r) + "\n" for r in replies), encoding="utf-8")
    stats = finetune.import_replies(ctx, tmp_path, tmp_path / "replies.jsonl", "offline", players_per_club=1)
    assert stats["accepted"] >= 1
    ex = [json.loads(x) for x in (tmp_path / "examples.jsonl").read_text("utf-8").splitlines()]
    assert all(e["sample"] == 1 and not e["first_try"] and e["teacher"] == "offline" for e in ex)
    assert all("<think>" not in e["messages"][2]["content"] for e in ex)
    # imported prompts are done: a second import adds nothing
    assert sum(finetune.import_replies(ctx, tmp_path, tmp_path / "replies.jsonl", "o", 1).values()) == 0
