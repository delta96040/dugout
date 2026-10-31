"""Merge the writer fine-tune's bench runs and training metrics into docs/backtests/writer_finetune.json.

    uv run python tools/finetune/report.py [--kernel dugout-writer-lora-1-5b]

Reads docs/backtests/writer_finetune_bench_*.json (one `dugout-agents bench-writer --clubs heldout` run per model),
data/finetune/stats.json and the kernel's metrics.json; copies the loss curve (CSV + PNG) to
docs/backtests/writer_finetune/; prints the comparison table for writer_finetune.md and the model card.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BT = ROOT / "docs" / "backtests"
LABELS = {
    "groq:qwen/qwen3.8-27b": "Groq Qwen3.8-27B (teacher, hosted)",
    "local:qwen2.5-1.5b-8k": "Qwen2.5-1.5B-Instruct (base, local)",
    "local:dugout-writer-1.5b": "dugout-writer-1.5b (fine-tuned, local)",
    "local:qwen2.5:3b": "Qwen2.5-3B-Instruct (current local fallback)",
}
# accepted notes that copy the fact-card layout instead of writing prose: the gate passes them (every sentence is
# bound and verified) but nobody would publish them by choice
CARD_ECHO = re.compile(r"Facts:|\(code |Example:|statement\)|reading:")
KEYS = ("sections", "accepted", "first_try", "stray_number_drafts", "llm_errors", "seconds_per_section")


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """95% Wilson interval for k successes in n."""
    if not n:
        return [0.0, 0.0]
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(c - h, 3), round(c + h, 3)]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--kernel", default="dugout-writer-lora-1-5b")
    a = p.parse_args()
    models, clubs = {}, set()
    for f in sorted(BT.glob("writer_finetune_bench_*.json")):
        rep = json.loads(f.read_text(encoding="utf-8"))
        clubs.update(rep["clubs"])
        for spec, r in rep["results"].items():
            accepted = [x["text"] for x in r["rows"] if x["accepted"]]
            models[spec] = {
                "label": LABELS.get(spec, spec),
                "run": rep["generated_at"],
                **{k: r[k] for k in KEYS},
                "card_echo_notes": sum(bool(CARD_ECHO.search(t)) for t in accepted),
                "accepted_ci95": wilson(len(accepted), r["sections"]),
                "rate_limit_wait_seconds": r.get("rate_limit_wait_seconds", 0),
            }
    kdir = ROOT / "data" / "finetune" / "kaggle" / a.kernel
    metrics = (
        json.loads((kdir / "metrics.json").read_text(encoding="utf-8"))
        if (kdir / "metrics.json").exists()
        else {}
    )
    out_dir = BT / "writer_finetune"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("loss_curve.csv", "loss_curve.png"):
        if (kdir / name).exists():
            shutil.copy(kdir / name, out_dir / name)
    stats = json.loads((ROOT / "data" / "finetune" / "stats.json").read_text(encoding="utf-8"))
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "heldout_clubs": sorted(clubs),
        "models": models,
        "dataset": {
            k: stats[k]
            for k in (
                "examples",
                "clubs",
                "by_agent",
                "by_league",
                "first_try",
                "duplicates_dropped",
                "attempted",
                "teacher_calls",
                "teacher",
            )
        },
        "training": {
            k: metrics.get(k)
            for k in (
                "examples",
                "val_loss_base",
                "val_loss_finetuned",
                "train_seconds",
                "gpu",
                "config",
                "versions",
            )
        },
    }
    (BT / "writer_finetune.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "| Writer | Accepted | First try | Drafts with stray numbers | Accepted notes copying the card layout | LLM errors | s / note |"
    )
    print("|---|---|---|---|---|---|---|")
    for m in models.values():
        print(
            f"| {m['label']} | {m['accepted']:.0%} | {m['first_try']:.0%} | {m['stray_number_drafts']} | "
            f"{m['card_echo_notes']} | {m['llm_errors']} | {m['seconds_per_section']} |"
        )
    print(
        f"\n{len(clubs)} held-out clubs, "
        f"{next(iter(models.values()))['sections'] if models else 0} notes per writer"
    )


if __name__ == "__main__":
    main()
