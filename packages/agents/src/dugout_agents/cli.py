"""`dugout-agents`: ask a question, build club dossiers, run the evaluation."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from dugout_pipeline.settings import REPO_ROOT

CLUBS_DIR = REPO_ROOT / "data" / "clubs"


def _ctx(args):
    from dugout_agents.llm import from_settings
    from dugout_agents.runtime import make_context

    return make_context(llm=None if args.no_llm else from_settings(), db=args.db)


def cmd_ask(args) -> int:
    from dugout_agents.graph import run

    ctx = _ctx(args)
    state = run(ctx, args.question, args.club)
    print(f"route: {state['agents']}  club: {state['club']}  focus: {state.get('focus')}\n")
    for sec in [*state["sections"], state["summary"]]:
        print(f"== {sec.title} [{sec.status}, {sec.writer}]")
        print(sec.text or "(nothing published)")
        for w in sec.withheld:
            print(f"   withheld ({w['status']}): {w['reason']}")
        print()
    return 0


def cmd_dossier(args) -> int:
    from dugout_agents import dossier
    from dugout_pipeline.onboard import showcase_clubs
    from dugout_pipeline.settings import get_settings

    ctx = _ctx(args)
    clubs = args.clubs
    llm_clubs = set(clubs)
    if args.all or args.league:
        # every club (of --league, if given); the LLM (free tier, small daily quota) writes for the showcase
        # clubs only, templates for the rest
        clubs = [
            c
            for (c,) in ctx.toolbox.con.execute(
                "SELECT club_id FROM clubs WHERE ? IS NULL OR league = ? ORDER BY league, club_id",
                [args.league, args.league],
            ).fetchall()
        ]
        llm_clubs = set(showcase_clubs(get_settings().config_dir))
    llm = ctx.llm
    ok = True
    for club in clubs:
        ctx.llm = llm if club in llm_clubs else None
        t0 = time.monotonic()
        doc, _ = dossier.build(ctx, club)
        v = doc["verification"]
        if v["sections_published"] == 0 or not v["stat_auditor"]["ok"]:
            print(f"{club}: nothing passed verification; not written", file=sys.stderr)
            ok = False
            continue
        path = dossier.write(doc, CLUBS_DIR)
        print(f"{club}: {path.relative_to(REPO_ROOT)}  {time.monotonic() - t0:.0f}s  {v}")
    return 0 if ok else 1


def cmd_eval(args) -> int:
    from dugout_agents.evaluate import run_eval

    ctx = _ctx(args)
    suffix = "" if args.set == "questions" else f"_{args.set}"
    name = args.name or ("agents_eval_templates" if args.no_llm else "agents_eval_llm") + suffix
    report = run_eval(
        ctx, out_dir=REPO_ROOT / "docs" / "backtests", limit=args.limit, name=name, question_set=args.set
    )
    print({k: v for k, v in report["summary"].items()})
    return 0 if report["summary"]["untraceable_number_rate"] == 0 else 1


def cmd_bench_writer(args) -> int:
    from dugout_agents.bench_writer import run

    ctx = _ctx(argparse.Namespace(no_llm=True, db=args.db))
    r = run(ctx, args.models.split(","), args.clubs.split(","), out_dir=REPO_ROOT / "docs" / "backtests")
    for m, x in r["results"].items():
        print(m, {k: v for k, v in x.items() if k != "rows"})
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
    p = argparse.ArgumentParser(prog="dugout-agents")
    p.add_argument("--db")
    p.add_argument("--no-llm", action="store_true", help="deterministic templates only")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ask")
    s.add_argument("question")
    s.add_argument("--club", default="liverpool")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("dossier")
    s.add_argument("clubs", nargs="*", default=["liverpool"])
    s.add_argument(
        "--all",
        action="store_true",
        help="every club in every league: showcase clubs with the LLM, the rest templates",
    )
    s.add_argument("--league", help="every club of one league (config/leagues.yaml key)")
    s.set_defaults(fn=cmd_dossier)
    s = sub.add_parser("eval")
    s.add_argument("--limit", type=int)
    s.add_argument("--name", help="report name in docs/backtests (default by writer mode)")
    s.add_argument("--set", default="questions", choices=["questions", "holdout"], help="question set")
    s.set_defaults(fn=cmd_eval)
    s = sub.add_parser("bench-writer", help="compare LLMs on how often their notes pass the publish gate")
    s.add_argument(
        "--models", required=True, help="comma list of <provider>:<model> (groq, openrouter, local)"
    )
    s.add_argument("--clubs", default="liverpool,arsenal")
    s.set_defaults(fn=cmd_bench_writer)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
