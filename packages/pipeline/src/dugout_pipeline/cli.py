"""`dugout` command line. Every Makefile target calls this, so Windows users can run `uv run dugout <cmd>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from dugout_pipeline.settings import get_settings


def _ctx(args, **overrides):
    from dugout_pipeline.adapters.base import IngestContext
    from dugout_pipeline.db import connect

    settings = get_settings()
    path = getattr(args, "db", None) or settings.warehouse_path
    return IngestContext(con=connect(path), league=getattr(args, "league", "premier_league"), **overrides)


def cmd_ingest(args) -> int:
    from dugout_pipeline.registry import JOBS
    from dugout_pipeline.runner import format_report, run

    sources = args.only.split(",") if args.only else JOBS[args.job]
    if args.skip:
        sources = [s for s in sources if s not in args.skip.split(",")]
    ctx = _ctx(args)
    job = run(ctx, sources)
    print(format_report(job))
    _write_summary(format_report(job))
    return 0 if job.ok else 1


def cmd_status(args) -> int:
    from dugout_pipeline.db import table_counts
    from dugout_pipeline.http import DailyBudget

    ctx = _ctx(args)
    for table, n in table_counts(ctx.con).items():
        print(f"{table:<22}{n:>10}")
    s = ctx.settings
    for src, limit in (("api_football", 90), ("guardian", 450)):
        b = DailyBudget(s.cache_dir / "budget" / f"{src}.json", limit)
        print(f"budget {src:<14} used today {b.used()}/{limit}")
    rows = ctx.con.execute("""SELECT source, ok, finished_at, message FROM source_runs
                              QUALIFY row_number() OVER (PARTITION BY source ORDER BY finished_at DESC) = 1
                              ORDER BY source""").fetchall()
    for src, ok, at, msg in rows:
        print(f"last run {src:<22}{'ok' if ok else 'FAILED':<8}{at:%Y-%m-%d %H:%M}  {msg or ''}")
    return 0


def cmd_load_open(args) -> int:
    from dugout_pipeline.loaders.open_data import LOADERS

    ctx = _ctx(args)
    kwargs = (
        {"limit": args.limit}
        if args.limit is not None and args.dataset in ("statsbomb", "wyscout", "pff")
        else {}
    )
    print(args.dataset, LOADERS[args.dataset](ctx.con, **kwargs))
    return 0


def cmd_refresh_values(args) -> int:
    if os.environ.get("CI") or get_settings().ci:
        print(
            "refresh-values scrapes Transfermarkt and must only run locally (CI is set). Refusing.",
            file=sys.stderr,
        )
        return 2
    from dugout_pipeline.runner import format_report, run

    job = run(_ctx(args), ["fpl", "transfermarkt", "transfermarkt_file"])
    print(format_report(job))
    print("Commit data/reference/transfermarkt_squads.csv so CI can use it.")
    return 0 if all(r.ok for r in job.results) else 1


def cmd_backfill(args) -> int:
    from dugout_pipeline.adapters.api_football import ApiFootballAdapter

    ctx = _ctx(args)
    a = ApiFootballAdapter(ctx)
    try:
        print(a.backfill_season(args.season, max_calls=args.max_calls))
    finally:
        a.close()
    return 0


def cmd_record(args) -> int:
    """Run one adapter live and save trimmed fixtures for tests. Uses a throwaway cache and DB."""
    from dugout_pipeline.adapters.base import IngestContext
    from dugout_pipeline.db import connect
    from dugout_pipeline.registry import build
    from dugout_pipeline.testing import TRIMMERS, RecordingTransport

    with tempfile.TemporaryDirectory() as tmp:
        settings = get_settings().model_copy(update={"cache_dir": Path(tmp)})
        con = connect()
        if args.source != "fpl":  # most adapters need clubs/players first: replay the recorded FPL fixtures
            from dugout_pipeline.testing import ReplayTransport

            seed = IngestContext(con=con, settings=settings, transport=ReplayTransport("fpl"))
            build("fpl", seed).ingest()
        ctx = IngestContext(
            con=con, settings=settings, transport=RecordingTransport(args.source, TRIMMERS.get(args.source))
        )
        adapter = build(args.source, ctx)
        try:
            print(adapter.ingest(**getattr(adapter, "record_kwargs", {})))
        finally:
            adapter.close()
    print(f"fixtures written to packages/pipeline/tests/fixtures/{args.source}/")
    return 0


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("```\n" + text + "\n```\n")


def cmd_setup_club(args) -> int:
    """Find and vet a club's official news source, save it, and optionally build everything for the club."""
    import subprocess

    from dugout_pipeline.onboard import SetupError, discover, save

    ctx = _ctx(args)
    ctx.load_clubs()
    slug = ctx.clubs.resolve(args.club)
    if slug is None:
        known = ", ".join(r[0] for r in ctx.con.execute("SELECT club_id FROM clubs ORDER BY 1").fetchall())
        print(f"unknown club {args.club!r}; clubs in this league: {known}", file=sys.stderr)
        return 2
    try:
        entry = discover(ctx, slug)
    except SetupError as exc:
        print(f"{slug}: {exc}", file=sys.stderr)
        return 1
    path = save(ctx, entry)
    print(f"{slug}: news from {entry['sitemap']} ({entry['path_prefix']}) saved to {path.name}")
    ctx.con.close()
    if not args.build:
        print(
            "next: dugout ingest --only club_news; dugout-models publish market; dugout-agents dossier "
            + slug
        )
        return 0
    steps = [
        [sys.executable, "-m", "dugout_pipeline.cli", "ingest", "--only", "club_news"],
        [sys.executable, "-m", "dugout_models.cli", "publish", "market"],
        [sys.executable, "-m", "dugout_agents.cli", "dossier", slug],
    ]
    for cmd in steps:
        print("running:", " ".join(cmd[2:]))
        if subprocess.run(cmd, check=False).returncode != 0:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s"
    )
    p = argparse.ArgumentParser(prog="dugout")
    p.add_argument("--db", help="warehouse path (default data/warehouse/dugout.duckdb)")
    p.add_argument("--league", default="premier_league")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="run a job (nightly | injuries | history) or selected sources")
    s.add_argument("--job", default="nightly", choices=["nightly", "injuries", "history"])
    s.add_argument("--only", help="comma-separated sources")
    s.add_argument("--skip", help="comma-separated sources to skip")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("status", help="table counts, API budgets, last run per source")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("load-open", help="load an open dataset for training/backtests")
    s.add_argument("dataset", choices=["statsbomb", "wyscout", "metrica", "skillcorner", "pff"])
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_load_open)

    s = sub.add_parser("refresh-values", help="LOCAL ONLY: polite Transfermarkt squad refresh")
    s.set_defaults(fn=cmd_refresh_values)

    s = sub.add_parser("backfill-api-football", help="slowly backfill a free-plan season (2022-2024)")
    s.add_argument("--season", type=int, required=True, choices=[2022, 2023, 2024])
    s.add_argument("--max-calls", type=int, default=40)
    s.set_defaults(fn=cmd_backfill)

    s = sub.add_parser("record", help="record trimmed test fixtures for one source (live)")
    s.add_argument("source")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("setup-club", help="set dugout up for a club: find and vet its official news source")
    s.add_argument("club", help="club name or slug, e.g. 'Arsenal' or 'nottingham-forest'")
    s.add_argument(
        "--build", action="store_true", help="then ingest its news, publish models, build its dossier"
    )
    s.set_defaults(fn=cmd_setup_club)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
