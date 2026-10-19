"""Fine-tuning data for the writer: fact cards -> notes that passed the publish gate.

A teacher LLM (Groq Qwen, the best writer in docs/backtests/writer_llm.md; when its daily token cap is reached,
the next open-weight model in the `--teacher` chain, recorded per example) gets each prompt exactly as production
builds it (`writer.SYSTEM`, `writer.USER`, `writer.card`). A pair is kept only when the draft passes the writer's
checks *and* the full publish gate (Stat Auditor + Fact-Check, nothing withheld). The target is the teacher's raw
reply: prose with fact codes ([F3]) and no numbers, so a student learns the same contract. A draft accepted after
the one retry is paired with the *first* prompt (the student should get it right first time).

Clubs are split by league (a hash, so the split is stable): test clubs never reach training (the writer benchmark
runs on them), validation clubs give the eval loss. See docs/backtests/writer_finetune.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dugout_agents import writer
from dugout_agents.facts import Fact
from dugout_agents.graph import SUMMARY_FACTS_PER_SECTION, Context, Section, gate
from dugout_agents.llm import LLM
from dugout_agents.specialists import CLUB_LEVEL, SPECIALISTS

log = logging.getLogger(__name__)

SEED = "dugout-writer-sft-v1"
# writer prompts per club: the prose specialists' dossier questions, the director's summary, questions about a player
PLAYER_QUESTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    # kind: (specialist, question templates); {player} is the focus player, {club} the club asked about
    "player_club": (
        "club",
        ("How is {player} doing this season?", "What has {player} done for {club} so far?"),
    ),
    "player_transfer": (
        "transfer",
        ("What is {player} worth?", "Could {club} sell {player}, and at what price?"),
    ),
    "player_news": ("news", ("Is {player} available for the next match?", "What is the latest on {player}?")),
    "potential": ("potential", ("How good could {player} become?", "What is {player}'s ceiling?")),
    "fit": ("fit", ("Would {player} fit at {club}?", "How much would {player} play at {club}?")),
}
DIRECTOR_QUESTION = "Summarise the dossier on {club} for the board."


def _h(*parts: str) -> str:
    return hashlib.sha1("|".join((SEED, *parts)).encode()).hexdigest()


@dataclass(frozen=True)
class Spec:
    """One writer prompt to build: cheap to list; facts are gathered only when it is taught."""

    club: str  # the club the note is about (the focus player's own club, or the asking club for `fit`)
    league: str
    kind: str  # a prose specialist name, "director", or a PLAYER_QUESTIONS key
    question: str
    player: str | None = None

    @property
    def id(self) -> str:
        return _h(self.club, self.kind, self.player or "", self.question)[:16]

    @property
    def agent(self) -> str:
        if self.kind == "director":
            return "director"
        return PLAYER_QUESTIONS[self.kind][0] if self.kind in PLAYER_QUESTIONS else self.kind


@dataclass
class Prompt:
    spec: Spec
    role: str
    club_name: str
    facts: list[Fact]
    sentences: int = 4
    template_limit: int = 8

    def messages(self) -> list[dict]:
        system = writer.SYSTEM.format(role=self.role, club=self.club_name, n=self.sentences)
        cards = "\n".join(writer.card(f) for f in self.facts)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": writer.USER.format(question=self.spec.question, cards=cards)},
        ]


# -- split -------------------------------------------------------------------------------------------------------


def split_clubs(clubs: dict[str, str], test_per_league: int = 2, val_per_league: int = 1) -> dict[str, str]:
    """club -> "train" | "val" | "test", a stable hash order within each league."""
    by_league: dict[str, list[str]] = {}
    for club, league in clubs.items():
        by_league.setdefault(league, []).append(club)
    out = {}
    for members in by_league.values():
        members.sort(key=lambda c: _h("split", c))
        for i, club in enumerate(members):
            out[club] = (
                "test" if i < test_per_league else "val" if i < test_per_league + val_per_league else "train"
            )
    return out


def league_clubs(ctx: Context) -> dict[str, str]:
    rows = ctx.toolbox.con.execute(
        "SELECT club_id, league FROM clubs WHERE season = (SELECT max(season) FROM clubs) ORDER BY club_id"
    ).fetchall()
    return dict(rows)


# -- prompts -----------------------------------------------------------------------------------------------------


def specs(
    ctx: Context, clubs: dict[str, str], players_per_club: int = 4, fit_per_club: int = 2
) -> list[Spec]:
    """Every prompt for these clubs, in waves (dossier sections, summaries, player questions), each wave in a
    stable shuffled order, so a run stopped early still covers clubs and kinds evenly."""
    con = ctx.toolbox.con
    squads: dict[str, list[str]] = {}
    for pid, club in con.execute(
        "SELECT player_id, club_id FROM players WHERE club_id IN (SELECT unnest(?)) ORDER BY player_id",
        [list(clubs)],
    ).fetchall():
        squads.setdefault(club, []).append(pid)
    waves: list[list[Spec]] = [[], [], []]
    for club, league in clubs.items():
        name = ctx.toolbox.name(club)
        for agent in CLUB_LEVEL:
            if SPECIALISTS[agent].prose:
                waves[0].append(
                    Spec(club, league, agent, SPECIALISTS[agent].dossier_question.format(club=name))
                )
        waves[1].append(Spec(club, league, "director", DIRECTOR_QUESTION.format(club=name)))
        squad = sorted(squads.get(club, []), key=lambda p: _h("player", p))[:players_per_club]
        others = sorted(
            (p for c, ps in squads.items() if c != club and clubs[c] == league for p in ps),
            key=lambda p: _h("fit", club, p),
        )[:fit_per_club]
        for kind, (_, templates) in PLAYER_QUESTIONS.items():
            for pid in others if kind == "fit" else squad:
                q = templates[int(_h(kind, pid), 16) % len(templates)]
                q = q.format(player=ctx.toolbox.name(pid), club=name)
                waves[2].append(Spec(club, league, kind, q, pid))
    return [s for wave in waves for s in sorted(wave, key=lambda s: s.id)]


def build(ctx: Context, spec: Spec) -> Prompt:
    """Gather the facts as production does (graph.specialist / graph.synthesise)."""
    tb = ctx.toolbox
    # fact codes count up within a run (F1 ... F300 in a dossier); start each prompt at a stable offset so the
    # student sees small and large codes alike
    tb._n = int(spec.id, 16) % 150
    if spec.kind == "director":
        sections = [SPECIALISTS[a].gather(tb, spec.club, []) for a in CLUB_LEVEL]
        facts = [f for fs in sections for f in [x for x in fs if x.eid][:SUMMARY_FACTS_PER_SECTION]]
        return Prompt(spec, "sporting director", tb.name(spec.club), facts, sentences=3, template_limit=4)
    s = SPECIALISTS[spec.agent]
    facts = s.gather(tb, spec.club, [spec.player] if spec.player else [])
    facts = facts[: s.writer_facts] if s.writer_facts else facts
    return Prompt(spec, s.role, tb.name(spec.club), facts, template_limit=s.max_facts)


# -- teaching ----------------------------------------------------------------------------------------------------


class Recorder:
    """Wraps the teacher and keeps its raw replies (the target is the reply, codes and all)."""

    def __init__(self, llm: LLM):
        self.llm, self.name, self.replies = llm, llm.name, []

    def complete(self, system: str, user: str) -> str:
        out = self.llm.complete(system, user)
        self.replies.append(out)
        return out

    @property
    def used(self) -> str:
        """The model that wrote the last reply (a FallbackLLM records it; a single LLM is itself)."""
        return getattr(self.llm, "used", self.llm.name)


def has_digits(target: str) -> bool:
    """A digit anywhere but inside a fact code: never a training target (the student must not learn numbers)."""
    return bool(re.search(r"\d", writer.CODE.sub(" ", target)))


@dataclass
class Outcome:
    status: str  # accepted | rejected | gate_failed | digits | no_facts | error
    example: dict | None = None
    errors: list[str] = field(default_factory=list)
    calls: int = 0


def teach(ctx: Context, llm: LLM, prompt: Prompt, teacher: str) -> Outcome:
    if not prompt.facts:
        return Outcome("no_facts")
    rec = Recorder(llm)
    draft = writer.write(
        rec,
        role=prompt.role,
        club=prompt.club_name,
        question=prompt.spec.question,
        facts=prompt.facts,
        ledger=ctx.ledger,
        checker=ctx.checker,
        allowed_names=ctx.allowed_names(),
        sentences=prompt.sentences,
        template_limit=prompt.template_limit,
    )
    errors = [e for a in draft.attempts for e in a.get("errors", [])]
    calls = len(rec.replies)
    if any("error" in a for a in draft.attempts):
        return Outcome("error", errors=[a["error"] for a in draft.attempts if "error" in a], calls=calls)
    if not draft.writer.startswith("llm"):
        return Outcome("rejected", errors=errors, calls=calls)
    sec = Section(prompt.spec.agent, prompt.spec.agent, prompt.spec.question, prompt.facts, draft.template)
    gate(sec, ctx)  # the publish gate itself: the note must go out whole
    if sec.status != "published" or sec.withheld:
        return Outcome("gate_failed", errors=[w["reason"] for w in sec.withheld], calls=calls)
    target = rec.replies[-1].strip()
    if has_digits(target):
        return Outcome("digits", calls=calls)
    s = prompt.spec
    example = {
        "id": s.id,
        "club": s.club,
        "league": s.league,
        "agent": s.agent,
        "kind": s.kind,
        "question": s.question,
        "messages": [*prompt.messages(), {"role": "assistant", "content": target}],
        "first_try": len(draft.attempts) == 1,
        "teacher": draft.writer.removeprefix("llm:"),  # the chain member that wrote the accepted note
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return Outcome("accepted", example, errors, calls)


class RateWatch:
    """Reads Groq's x-ratelimit-remaining-requests from each response (a daily bucket that refills steadily)."""

    def __init__(self) -> None:
        self.remaining: int | None = None

    def __call__(self, response) -> None:
        value = response.headers.get("x-ratelimit-remaining-requests")
        if value and value.isdigit():
            self.remaining = int(value)


def done_ids(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    out = set()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["status"] != "error":  # errors (rate limits, outages) are retried on the next run
            out.add(row["id"])
    return out


def generate(
    ctx: Context,
    llm: LLM,
    teacher: str,
    out_dir: Path,
    *,
    max_calls: int,
    floor: int = 200,
    players_per_club: int = 4,
    fit_per_club: int = 2,
    test_per_league: int = 2,
    val_per_league: int = 1,
    watch: RateWatch | None = None,
    sleep=time.sleep,
) -> Counter:
    """Teach every train/val prompt not done yet, appending to examples.jsonl and log.jsonl (resumable).

    Stops after `max_calls` teacher calls. When the provider's daily request bucket falls below `floor`, waits
    between calls so the nightly dossier run still finds requests left."""
    out_dir.mkdir(parents=True, exist_ok=True)
    clubs = league_clubs(ctx)
    split = split_clubs(clubs, test_per_league, val_per_league)
    (out_dir / "split.json").write_text(json.dumps(split, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    todo = specs(
        ctx, {c: lg for c, lg in clubs.items() if split[c] != "test"}, players_per_club, fit_per_club
    )
    done = done_ids(out_dir / "log.jsonl")
    stats: Counter = Counter()
    calls = errors_in_row = 0
    for spec in todo:
        if spec.id in done:
            continue
        if calls >= max_calls:
            break
        if watch and watch.remaining is not None and watch.remaining < floor:
            sleep(120)  # below the refill rate (about one request a minute and a half)
        t0 = time.monotonic()
        try:
            prompt = build(ctx, spec)
        except Exception as exc:  # a tool failing on odd data skips the prompt, never the run
            log.warning("%s %s: gather failed: %s", spec.club, spec.kind, exc)
            outcome = Outcome("no_facts", errors=[type(exc).__name__])
        else:
            outcome = teach(ctx, llm, prompt, teacher)
        calls += outcome.calls
        stats[outcome.status] += 1
        row = {
            "id": spec.id,
            "club": spec.club,
            "kind": spec.kind,
            "status": outcome.status,
            "calls": outcome.calls,
            "errors": outcome.errors[:4],
            "seconds": round(time.monotonic() - t0, 1),
        }
        with (out_dir / "log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if outcome.example:
            outcome.example["split"] = split[spec.club]
            with (out_dir / "examples.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(outcome.example, ensure_ascii=False) + "\n")
        errors_in_row = errors_in_row + 1 if outcome.status == "error" else 0
        if errors_in_row >= 5:
            log.warning("teacher failing repeatedly; stopping (resume later)")
            break
        log.info(
            "%s %s %s (%d calls, remaining %s)",
            spec.club,
            spec.kind,
            outcome.status,
            calls,
            watch.remaining if watch else "?",
        )
    return stats


# -- finalise ----------------------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", writer.CODE.sub("[F]", text)).strip().lower()


def read_examples(path: Path) -> Iterator[dict]:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def finalise(out_dir: Path) -> dict:
    """Dedupe (same prompt, or same note up to fact codes) and write train/val JSONL in chat format + stats."""
    split = json.loads((out_dir / "split.json").read_text(encoding="utf-8"))
    seen_prompt, seen_target, kept = set(), set(), []
    for ex in read_examples(out_dir / "examples.jsonl"):
        p = _norm(ex["messages"][0]["content"] + ex["messages"][1]["content"])
        t = _norm(ex["messages"][2]["content"])
        if p in seen_prompt or t in seen_target or split.get(ex["club"]) == "test":
            continue
        seen_prompt.add(p)
        seen_target.add(t)
        kept.append(ex)
    for name in ("train", "val"):
        rows = [e for e in kept if split[e["club"]] == name]
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for e in rows:
                meta = {k: e[k] for k in ("id", "club", "league", "agent", "kind", "first_try")}
                fh.write(json.dumps({"messages": e["messages"], **meta}, ensure_ascii=False) + "\n")
    log_rows = [json.loads(x) for x in (out_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    stats = {
        "teacher": Counter(e["teacher"] for e in kept),
        "clubs": Counter(split.values()),
        "examples": Counter(split[e["club"]] for e in kept),
        "by_agent": Counter(e["agent"] for e in kept),
        "by_league": Counter(e["league"] for e in kept),
        "first_try": sum(e["first_try"] for e in kept),
        "duplicates_dropped": sum(1 for _ in read_examples(out_dir / "examples.jsonl")) - len(kept),
        "attempted": Counter(r["status"] for r in log_rows),
        "teacher_calls": sum(r["calls"] for r in log_rows),
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return stats
