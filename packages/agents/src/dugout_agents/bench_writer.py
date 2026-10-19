"""Writer benchmark: which open-weight LLM writes notes that pass the publish gate?

Every candidate gets the same sections (the six club specialists for each club, with the dossier questions) and
the same rules. A draft counts only if it passes everything the publish gate checks (no stray numbers, bound
stats, Fact-Check, at least two facts stated). Reported per model: accepted on the first try, accepted after the
one retry, fell back to templates, seconds per section, API calls used, drafts that tried to write numbers.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from dugout_agents import llm as llm_mod
from dugout_agents import writer
from dugout_agents.graph import Context
from dugout_agents.llm import LLM
from dugout_agents.specialists import CLUB_LEVEL, SPECIALISTS
from dugout_pipeline.settings import get_settings


def make_llm(spec: str, patient: bool = False) -> LLM:
    """`<provider>:<model>` as in LLM_CHAIN (groq, openrouter, local).

    patient: wait out free-tier rate limits (Groq's rolling tokens-per-day cap) instead of failing after three
    retries; the time spent waiting is kept apart (`waited`) so seconds per note measure the model, not the quota."""
    llm = llm_mod.make_llm(spec, get_settings().llm_local_base_url)
    if llm is None:
        raise SystemExit(f"{spec}: the provider's API key is not set in .env")
    llm.waited = 0.0
    if patient:
        llm.retries = 500

        def sleep(seconds: float, _sleep=llm._sleep) -> None:
            llm.waited += seconds
            _sleep(seconds)

        llm._sleep = sleep
    return llm


def sections(ctx: Context, clubs: list[str]) -> list[dict]:
    out = []
    for club in clubs:
        for name in CLUB_LEVEL:
            spec = SPECIALISTS[name]
            if not spec.prose:  # the lineup is always templated in production
                continue
            facts = spec.gather(ctx.toolbox, club, [])
            facts = facts[: spec.writer_facts] if spec.writer_facts else facts  # as graph.specialist does
            if facts:
                q = spec.dossier_question.format(club=ctx.toolbox.name(club))
                out.append({"club": club, "agent": name, "role": spec.role, "question": q, "facts": facts})
    return out


def run(
    ctx: Context,
    models: list[str],
    clubs: list[str],
    out_dir: Path | None = None,
    name: str = "writer_llm",
    patient: bool = False,
) -> dict:
    secs = sections(ctx, clubs)
    results: dict[str, dict] = {}
    for spec in models:
        llm = make_llm(spec, patient)
        rows = []
        for s in secs:
            t0, waited = time.time(), llm.waited
            d = writer.write(
                llm,
                role=s["role"],
                club=ctx.toolbox.name(s["club"]),
                question=s["question"],
                facts=s["facts"],
                ledger=ctx.ledger,
                checker=ctx.checker,
                allowed_names=ctx.allowed_names(),
                template_limit=SPECIALISTS[s["agent"]].max_facts,
            )
            ok = d.writer.startswith("llm")
            rows.append(
                {
                    "club": s["club"],
                    "agent": s["agent"],
                    "accepted": ok,
                    "first_try": ok and len(d.attempts) == 1,
                    "seconds": round(time.time() - t0 - (llm.waited - waited), 1),
                    "rate_limit_wait": round(llm.waited - waited, 1),
                    "llm_error": any("error" in a for a in d.attempts),
                    "stray": sum(
                        any("numbers written directly" in e for e in a.get("errors", [])) for a in d.attempts
                    ),
                    "errors": [e for a in d.attempts for e in a.get("errors", [])][:4],
                    "text": d.template if ok else "",
                }
            )
        n = len(rows)
        results[spec] = {
            "sections": n,
            "accepted": sum(r["accepted"] for r in rows) / n,
            "first_try": sum(r["first_try"] for r in rows) / n,
            "llm_errors": sum(r["llm_error"] for r in rows),
            "stray_number_drafts": sum(r["stray"] for r in rows),
            "seconds_per_section": round(sum(r["seconds"] for r in rows) / n, 1),
            "api_calls": getattr(llm, "calls", None),
            "rate_limit_wait_seconds": round(llm.waited),
            "rows": rows,
        }
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "clubs": clubs,
        "results": results,
    }
    if out_dir:
        write_report(report, out_dir, name)
    return report


def write_report(report: dict, out_dir: Path, name: str = "writer_llm") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Writer benchmark: which LLM writes notes that pass the publish gate",
        "",
        f"Generated {report['generated_at']}. Sections: the six club specialists' dossier questions for "
        f"{', '.join(report['clubs'])}. A draft counts only if it passes every gate check; otherwise the section falls "
        "back to deterministic templates (numbers are never affected either way).",
        "",
        "| Model | Accepted | First try | Drafts with stray numbers | LLM errors | s / section | API calls |",
        "|---|---|---|---|---|---|---|",
    ]
    for m, r in report["results"].items():
        lines.append(
            f"| {m} | {r['accepted']:.0%} | {r['first_try']:.0%} | {r['stray_number_drafts']} | {r['llm_errors']} | "
            f"{r['seconds_per_section']} | {r['api_calls']} |"
        )
    lines += ["", "## Sample accepted notes", ""]
    for m, r in report["results"].items():
        sample = next((x for x in r["rows"] if x["accepted"]), None)
        if sample:
            lines += [
                f"**{m}** ({sample['club']}, {sample['agent']}, placeholders unrendered):",
                "",
                f"> {sample['text']}",
                "",
            ]
    (out_dir / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
