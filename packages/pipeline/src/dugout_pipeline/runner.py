"""Runs a list of adapters, records each run in `source_runs`, and decides whether the job failed."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from dugout_pipeline.adapters.base import IngestContext, MissingCredential, SourceDisabled
from dugout_pipeline.clubs import ClubResolver, league_config
from dugout_pipeline.registry import ADAPTERS, build

log = logging.getLogger(__name__)


@dataclass
class SourceResult:
    source: str
    ok: bool
    rows: dict[str, int] = field(default_factory=dict)
    message: str = ""
    seconds: float = 0.0
    required: bool = False
    skipped: bool = False


@dataclass
class JobResult:
    results: list[SourceResult]

    @property
    def failed_required(self) -> list[SourceResult]:
        return [r for r in self.results if r.required and not r.ok and not r.skipped]

    @property
    def ok(self) -> bool:
        return not self.failed_required


def run(ctx: IngestContext, sources: list[str]) -> JobResult:
    """Run `sources` for the context's league, in order."""
    return JobResult([_run_one(ctx, name, name, ADAPTERS[name].required) for name in sources])


def takes(league: str, cfg: dict, source: str, default: bool) -> bool:
    """Whether a league-scoped source runs for this league: the default league runs every source of the job,
    the others only the ones their block lists under `sources`; either way the league needs the provider id."""
    cls = ADAPTERS[source]
    if cls.requires and not cfg.get("provider_ids", {}).get(cls.requires):
        return False
    return default or source in cfg.get("sources", [])


def run_leagues(ctx: IngestContext, sources: list[str], league_keys: list[str]) -> JobResult:
    """Run a job across leagues. Sources keep the job's order (football-data.org creates every league's clubs
    before Wikidata looks them up); a global source runs once. Only the default league's failures fail the job:
    another league's dossiers keep yesterday's data instead."""
    default = league_keys[0]
    results = []
    for name in sources:
        cls = ADAPTERS[name]
        if cls.scope == "global":
            results.append(_run_one(replace(ctx, league=default), name, name, cls.required))
            continue
        for lg in league_keys:
            if not takes(lg, league_config(lg), name, lg == default):
                continue
            sub = replace(ctx, league=lg, clubs=ClubResolver.from_config())
            label = name if lg == default else f"{name}@{lg}"
            results.append(_run_one(sub, name, label, cls.required and lg == default))
    return JobResult(results)


def _run_one(ctx: IngestContext, name: str, label: str, required: bool) -> SourceResult:
    started, t0 = datetime.now(UTC), time.monotonic()
    res = SourceResult(label, ok=False, required=required)
    adapter = None
    try:
        adapter = build(name, ctx)
        res.rows = adapter.ingest() or {}
        res.ok = True
    except (SourceDisabled, MissingCredential) as exc:
        res.skipped, res.message = True, str(exc)
    except Exception as exc:  # one source failing must not take down the others
        log.exception("source %s failed", label)
        res.message = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if adapter:
            adapter.close()
    res.seconds = round(time.monotonic() - t0, 1)
    ctx.con.execute(
        "INSERT OR REPLACE INTO source_runs VALUES (?,?,?,?,?,?,?)",
        [
            ctx.run_id,
            label,
            started,
            datetime.now(UTC),
            res.ok,
            sum(res.rows.values()),
            res.message or None,
        ],
    )
    return res


def format_report(job: JobResult) -> str:
    lines = [f"{'source':<36}{'status':<10}{'secs':>7}  rows / message"]
    for r in job.results:
        status = "skipped" if r.skipped else "ok" if r.ok else ("FAILED" if r.required else "failed")
        detail = ", ".join(f"{k}={v}" for k, v in r.rows.items()) if r.ok else r.message
        lines.append(f"{r.source:<36}{status:<10}{r.seconds:>7}  {detail}")
    lines.append(
        "job: "
        + (
            "OK"
            if job.ok
            else "FAILED (required source failed: " + ", ".join(r.source for r in job.failed_required) + ")"
        )
    )
    return "\n".join(lines)
