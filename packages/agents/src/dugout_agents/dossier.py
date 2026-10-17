"""`data/clubs/<slug>.json`: the verified club dossier the website reads.

Text is stored as *segments* so the site can make every number hoverable: {"text": ...} or {"stat": id, "display":
...}. Each cited stat carries value, source, URL, fetch time and verification status. Only sections that passed
the Stat Auditor and Fact-Check are included; withheld material is listed (without numbers) for transparency.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dugout_agents.facts import MODEL_DOCS
from dugout_agents.graph import Context, Section, State, run
from dugout_agents.specialists import CLUB_LEVEL
from dugout_verify.auditor import audit
from dugout_verify.ledger import PLACEHOLDER, format_value

SCHEMA = "dugout.club/1"


def segments(template: str, ctx: Context) -> list[dict]:
    out: list[dict] = []
    pos = 0
    for m in PLACEHOLDER.finditer(template):
        if m.start() > pos:
            out.append({"text": template[pos : m.start()]})
        ev = ctx.ledger.get(m.group(1))
        out.append({"stat": m.group(1), "display": format_value(ev, m.group(2))})
        pos = m.end()
    if pos < len(template):
        out.append({"text": template[pos:]})
    return out


def _strip_numbers(text: str) -> str:
    return PLACEHOLDER.sub("[number]", text)


def section_json(sec: Section, ctx: Context) -> dict:
    claims = []
    for c in (sec.factcheck or {}).get("claims", []):
        if c["status"] not in ("verified", "analysis"):
            continue
        claims.append(
            {
                "segments": segments(c["text"], ctx),
                "type": c["type"],
                "status": c["status"],
                "reason": c["reason"],
                "sources": [
                    {k: s.get(k) for k in ("outlet", "tier", "url", "official")} for s in c.get("support", [])
                ],
            }
        )
    return {
        "agent": sec.agent,
        "title": sec.title,
        "question": sec.question,
        "status": sec.status,
        "writer": sec.writer,
        "claims": claims if sec.status == "published" else [],
        "withheld": [{**w, "text": _strip_numbers(w["text"])} for w in sec.withheld],
    }


def stat_json(eid: str, ctx: Context, status: dict[str, str], cross: dict[str, list]) -> dict:
    ev = ctx.ledger.get(eid)
    return {
        "value": ev.value,
        "display": format_value(ev, None),
        "unit": ev.unit,
        "metric": ev.metric,
        "entity_type": ev.entity_type,
        "entity_id": ev.entity_id,
        "entity_name": ctx.toolbox.name(ev.entity_id),
        "source": ev.source,
        "source_url": ev.source_url,
        "source_tier": ev.source_tier,
        "fetched_at": ev.fetched_at.isoformat(),
        "query_kind": ev.query.get("kind"),
        "status": status.get(eid, "unknown"),
        "cross_checks": cross.get(eid, []),
    }


def key_stats(ctx: Context, sections: list[Section]) -> tuple[dict, dict, dict]:
    """The structured facts behind each section (for charts and the 3D story), whether or not the prose cited them.

    They pass the same Stat Auditor as prose numbers (re-run, cross-source); a fact that fails is left out.
    Returns ({agent: [{stat, display, label, subject}]}, status by id, cross-checks by id)."""
    out: dict[str, list[dict]] = {}
    status: dict[str, str] = {}
    cross: dict[str, list] = {}
    for sec in sections:
        rows = []
        for f in sec.facts:
            for eid, fmt in [(f.eid, f.fmt), *([f.when] if f.when else [])]:
                if not eid:
                    continue
                rep = audit(
                    f"{{{{stat:{eid}}}}}",
                    ctx.ledger,
                    resolve_body=ctx.resolve_body,
                    allowed_names=ctx.allowed_names(),
                    files_root=ctx.files_root,
                )
                if not rep.ok:
                    continue
                status.update(rep.stat_status)
                cross.update(rep.cross_checks)
                rows.append(
                    {
                        "stat": eid,
                        "display": format_value(ctx.ledger.get(eid), fmt),
                        "label": f.label,
                        "subject": f.subject,
                    }
                )
        out[sec.agent] = rows
    return out, status, cross


def build(ctx: Context, club: str) -> tuple[dict, State]:
    state = run(ctx, "", club, agents=CLUB_LEVEL)
    sections = sorted(state["sections"], key=lambda s: CLUB_LEVEL.index(s.agent))
    everything = [state["summary"], *sections]
    published = [s for s in everything if s.status == "published"]
    status: dict[str, str] = {}
    cross: dict[str, list] = {}
    totals = {"numbers_total": 0, "numbers_untraceable": 0}
    fc_counts: dict[str, int] = {}
    for s in everything:
        if s.audit:
            status.update(s.audit["stat_status"])
            cross.update(s.audit["cross_checks"])
        if s.status == "published":
            totals["numbers_total"] += len(s.cited)
        for k, v in ((s.factcheck or {}).get("counts") or {}).items():
            fc_counts[k] = fc_counts.get(k, 0) + v
    cited = sorted({e for s in published for e in s.cited})
    lg = ctx.toolbox.league(club)
    key, key_status, key_cross = key_stats(ctx, sections)
    status.update(key_status)
    cross.update(key_cross)
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "club": {"slug": club, "name": ctx.toolbox.name(club), "season": ctx.toolbox.season},
        "league": {k: lg[k] for k in ("key", "name", "country")},
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_as_of": ctx.toolbox.now.isoformat(timespec="seconds"),
        "llm": ctx.llm.name if ctx.llm else None,
        "summary": section_json(state["summary"], ctx),
        "sections": [section_json(s, ctx) for s in sections],
        "stats": {
            e: stat_json(e, ctx, status, cross)
            for e in sorted(set(cited) | {k["stat"] for v in key.values() for k in v})
        },
        "key_stats": key,
        "verification": {
            "stat_auditor": {"ok": all(s.audit["ok"] for s in published if s.audit), **totals},
            "fact_check_claims": fc_counts,
            "sections_published": sum(s.status == "published" for s in everything),
            "sections_withheld": sum(s.status == "withheld" for s in everything),
        },
        "methods": MODEL_DOCS,
    }
    doc["names"] = digit_names(doc, ctx.allowed_names())
    return doc, state


def digit_names(doc: dict, names: list[str]) -> list[str]:
    """Names with digits in them ("Parma Calcio 1913") that the published prose uses.

    The Stat Auditor allows digits inside registered names; the site marks these so its digit check can too.
    """
    text = " ".join(
        seg.get("text", "")
        for s in [doc["summary"], *doc["sections"]]
        for c in s["claims"]
        for seg in c["segments"]
    )
    return sorted({n for n in names if re.search(r"\d", n) and n in text}, key=len, reverse=True)


def write(doc: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{doc['club']['slug']}.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def write_leagues(path: Path) -> Path:
    """`data/leagues.json`: the configured leagues in config order, for the site's league picker."""
    from dugout_pipeline.clubs import league_config, leagues

    rows = [{"key": lg, **{k: league_config(lg)[k] for k in ("name", "country")}} for lg in leagues()]
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
