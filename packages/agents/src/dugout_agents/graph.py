"""LangGraph orchestration: Sporting Director -> specialists (in parallel) -> synthesis -> verification gate.

    route ──Send──► specialist (x N) ──► synthesise ──► verify ──► END

`verify` is the publish gate: a section is published only if the Stat Auditor passes and Fact-Check finds no
conflicting claim; unverified sentences are cut (and listed as withheld), never published.
"""

from __future__ import annotations

import operator
import threading
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from dugout_agents import writer
from dugout_agents.facts import Fact, Toolbox
from dugout_agents.llm import LLM
from dugout_agents.specialists import SPECIALISTS, find_club, find_players, route
from dugout_agents.tools import RUMOUR_RECORD
from dugout_verify.auditor import audit
from dugout_verify.factcheck import FactChecker
from dugout_verify.ledger import EvidenceLedger, render
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

SUMMARY_FACTS_PER_SECTION = 2


@dataclass
class Section:
    agent: str
    title: str
    question: str
    facts: list[Fact] = field(default_factory=list)
    template: str = ""
    writer: str = "template"
    attempts: list[dict] = field(default_factory=list)
    audit: dict | None = None
    factcheck: dict | None = None
    published_template: str = ""
    text: str = ""
    cited: list[str] = field(default_factory=list)
    withheld: list[dict] = field(default_factory=list)
    status: str = "draft"  # published | withheld | empty


class State(TypedDict, total=False):
    question: str
    club: str
    focus: list[str]
    agents: list[str]
    agent: str
    sections: Annotated[list[Section], operator.add]
    summary: Section


@dataclass
class Context:
    toolbox: Toolbox
    ledger: EvidenceLedger
    checker: FactChecker
    llm: LLM | None
    resolve_body: Any = None
    files_root: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def allowed_names(self) -> list[str]:
        return list(self.toolbox.names.values())


def build_graph(ctx: Context):
    tb = ctx.toolbox

    def route_node(state: State) -> dict:
        named = find_club(tb, state["question"])
        focus = state.get("focus") or find_players(tb, state["question"], named or state["club"])
        club = named or state["club"]
        if focus and not named:  # "How many goals has Haaland scored?" is about Haaland's club
            row = tb.con.execute("SELECT club_id FROM players WHERE player_id = ?", [focus[0]]).fetchone()
            club = row[0] if row and row[0] else club
        agents = state.get("agents") or route(state["question"])
        return {"club": club, "focus": focus, "agents": agents}

    def fan_out(state: State) -> list[Send]:
        return [Send("specialist", {**state, "agent": a}) for a in state["agents"]]

    def specialist(state: State) -> dict:
        spec = SPECIALISTS[state["agent"]]
        club = state["club"]
        with ctx.lock:  # one DuckDB connection, one local LLM: specialists take turns
            facts = spec.gather(tb, club, state.get("focus") or [])
        question = state["question"] or spec.dossier_question.format(club=tb.name(club))
        with ctx.lock:
            draft = writer.write(
                ctx.llm if spec.prose else None,
                role=spec.role,
                club=tb.name(club),
                question=question,
                facts=facts[: spec.writer_facts] if spec.writer_facts else facts,
                ledger=ctx.ledger,
                checker=ctx.checker,
                allowed_names=ctx.allowed_names(),
                template_limit=spec.max_facts,
            )
        sec = Section(spec.name, spec.title, question, facts, draft.template, draft.writer, draft.attempts)
        return {"sections": [sec]}

    def synthesise(state: State) -> dict:
        club = tb.name(state["club"])
        order = {name: i for i, name in enumerate(SPECIALISTS)}
        sections = sorted(state["sections"], key=lambda s: order[s.agent])
        question = state["question"] or f"Summarise the dossier on {club} for the board."
        if len(sections) < 2:  # a one-specialist answer needs no summary on top
            return {"summary": Section("director", "Sporting Director", question)}
        facts = [f for s in sections for f in [x for x in s.facts if x.eid][:SUMMARY_FACTS_PER_SECTION]]
        with ctx.lock:
            draft = writer.write(
                ctx.llm,
                role="sporting director",
                club=club,
                question=question,
                facts=facts,
                ledger=ctx.ledger,
                checker=ctx.checker,
                allowed_names=ctx.allowed_names(),
                sentences=3,
                template_limit=4,
            )
        return {
            "summary": Section(
                "director", "Sporting Director", question, facts, draft.template, draft.writer, draft.attempts
            )
        }

    def verify_node(state: State) -> dict:
        with ctx.lock:
            for sec in [*state["sections"], state["summary"]]:
                gate(sec, ctx)
        return {}

    g = StateGraph(State)
    g.add_node("route", route_node)
    g.add_node("specialist", specialist)
    g.add_node("synthesise", synthesise)
    g.add_node("verify", verify_node)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", fan_out, ["specialist"])
    g.add_edge("specialist", "synthesise")
    g.add_edge("synthesise", "verify")
    g.add_edge("verify", END)
    return g.compile()


def gate(sec: Section, ctx: Context) -> None:
    """Stat Auditor + Fact-Check; cut unverified sentences; publish only what passes both."""
    if not sec.template:
        sec.status = "empty"
        return
    names = ctx.allowed_names()
    rep = audit(
        sec.template,
        ctx.ledger,
        resolve_body=ctx.resolve_body,
        allowed_names=names,
        files_root=ctx.files_root,
    )
    sec.audit = rep.as_dict()
    fc = ctx.checker.check(sec.template, ctx.ledger, render=lambda t: render(t, ctx.ledger)[0])
    sec.factcheck = fc.as_dict()
    if not rep.ok or not fc.ok:
        sec.status = "withheld"
        sec.withheld = [
            {"text": c.text, "status": c.status, "reason": c.reason}
            for c in fc.claims
            if c.status in ("conflicting", "unverified")
        ] + [{"text": f.message, "status": "audit_error", "reason": f.check} for f in rep.errors]
        return
    # a rumour's track record ("past reports like this one about X") goes only where the rumour itself is published
    rumours = " ".join(c.text for c in fc.claims if c.type == "attributed" and c.status == "verified")
    for c in fc.claims:
        if c.status == "verified" and (m := RUMOUR_RECORD.match(c.text)) and m.group(1) not in rumours:
            c.status, c.reason = "unverified", "the rumour it is about was not confirmed"
    sec.factcheck = fc.as_dict()
    keep = [c for c in fc.claims if c.status in ("verified", "analysis")]
    sec.withheld = [
        {"text": c.text, "status": c.status, "reason": c.reason}
        for c in fc.claims
        if c.status not in ("verified", "analysis")
    ]
    sec.published_template = " ".join(c.text for c in keep)
    if not sec.published_template:
        sec.status = "withheld"
        return
    final = audit(
        sec.published_template,
        ctx.ledger,
        resolve_body=ctx.resolve_body,
        allowed_names=names,
        files_root=ctx.files_root,
    )
    if not final.ok:  # cannot happen when the full draft passed; kept as a hard guarantee
        sec.status = "withheld"
        return
    sec.text, sec.cited = render(sec.published_template, ctx.ledger)
    sec.status = "published"


def run(ctx: Context, question: str, club: str, agents: list[str] | None = None) -> State:
    graph = build_graph(ctx)
    return graph.invoke(
        {"question": question, "club": club, "agents": agents or [], "focus": [], "sections": []}
    )
