"""Publish the fine-tuned writer to the Hugging Face Hub: LoRA adapter, q4_K_M GGUF, model card.

    uv run python tools/finetune/publish_hf.py [--repo dugout-writer-qwen2.5-1.5b] [--private]

Reads HF_TOKEN (write role) from .env, never printed. The model card's numbers come from
docs/backtests/writer_finetune.json (written by tools/finetune/report.py), the same file the site cites.
Licence: Apache-2.0, as the base model (Qwen/Qwen2.5-1.5B-Instruct) and the teacher (Qwen/Qwen3.8-27B).
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from dugout_pipeline.settings import get_settings
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "data" / "finetune" / "kaggle" / "dugout-writer-lora-1-5b"
REPO_URL = "https://github.com/delta96040/dugout"


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def card(r: dict, repo_id: str) -> str:
    rows = "\n".join(
        f"| {m['label']} | {pct(m['accepted'])} | {pct(m['first_try'])} | {m['stray_number_drafts']} | "
        f"{m['seconds_per_section']} |"
        for m in r["models"].values()
    )
    t, d = r["training"], r["dataset"]
    ex = d["examples"]
    return f"""---
license: apache-2.0
base_model: Qwen/Qwen2.5-1.5B-Instruct
library_name: peft
language: [en]
tags: [lora, gguf, football, data-to-text, grounded-generation, dugout]
pipeline_tag: text-generation
---

# {repo_id.split("/")[-1]}

A LoRA fine-tune of [Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) that writes the
briefing notes of [dugout]({REPO_URL}), an open-source AI sporting director. It turns *fact cards* into short
prose and **never writes a number**: values appear only as fact codes (`[F3]`) that dugout's code replaces with
audited, sourced values. Every note must pass dugout's publish gate: no stray numbers, each number bound to its
subject and metric, and an NLI Fact-Check of every sentence.

## Results (held-out clubs, dugout's `bench-writer`)

{len(r["heldout_clubs"])} clubs never seen in training ({", ".join(r["heldout_clubs"])}), the dossier's five
written sections each. *Accepted* = passed every gate check (with production's one retry); *first try* = without
it; *stray numbers* = drafts that wrote a digit or number word (rejected by the auditor).

| Writer | Accepted | First try | Drafts with stray numbers | s / note |
|---|---|---|---|---|
{rows}

Local models ran on a laptop CPU through Ollama (q4_K_M, 8k context); Groq's time excludes free-tier rate-limit waits.
Full report: [writer_finetune.md]({REPO_URL}/blob/main/docs/backtests/writer_finetune.md).

## Training

- Data: {ex.get("train", 0)} train / {ex.get("val", 0)} validation notes (validation clubs held out too), built by
  `dugout-agents finetune-data`: production prompts answered by Qwen3.8-27B (the writer dugout uses on Groq; the
  bulk served from the same Apache-2.0 weights by llama.cpp on Kaggle), kept only if they passed the full gate.
  The dataset is not published (fact cards quote news headlines and rumours).
- LoRA r={t["config"]["lora_r"]}, alpha={t["config"]["lora_alpha"]}, all attention and MLP projections,
  {t["config"]["epochs"]} epochs, lr {t["config"]["lr"]}, effective batch
  {t["config"]["batch_size"] * t["config"]["grad_accum"]}, loss on the reply only, seed {t["config"]["seed"]}, one
  Kaggle {t["gpu"]} ({t["train_seconds"]} s). Validation loss {t["val_loss_base"]:.3f} (base) ->
  {t["val_loss_finetuned"]:.3f}.
- Code: [`tools/finetune/`]({REPO_URL}/tree/main/tools/finetune) (kernel, pinned versions).

## Files

- `adapter_model.safetensors`, `adapter_config.json`: the LoRA adapter (PEFT).
- `dugout-writer-q4_K_M.gguf`: the merged model, quantized for llama.cpp / Ollama.

```bash
ollama create dugout-writer -f Modelfile   # FROM ./dugout-writer-q4_K_M.gguf + Qwen2.5's chat template
```

## Limits

Trained for one narrow task and one prompt format (dugout's writer prompt); outside it, it is a small 1.5B model.
It can still bind a code to the wrong subject; dugout's gate rejects such notes and falls back to templates.
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default="dugout-writer-qwen2.5-1.5b")
    p.add_argument("--private", action="store_true")
    a = p.parse_args()
    api = HfApi(token=get_settings().secret("hf_token"))
    repo_id = f"{api.whoami()['name']}/{a.repo}"
    report = json.loads((ROOT / "docs" / "backtests" / "writer_finetune.json").read_text(encoding="utf-8"))
    api.create_repo(repo_id, private=a.private, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "README.md").write_text(card(report, repo_id), encoding="utf-8")
        api.upload_file(path_or_fileobj=str(Path(d) / "README.md"), path_in_repo="README.md", repo_id=repo_id)
    api.upload_folder(folder_path=str(KERNEL / "adapter"), repo_id=repo_id, commit_message="LoRA adapter")
    api.upload_file(
        path_or_fileobj=str(KERNEL / "dugout-writer-q4_K_M.gguf"),
        path_in_repo="dugout-writer-q4_K_M.gguf",
        repo_id=repo_id,
        commit_message="q4_K_M GGUF",
    )
    for name in ("loss_curve.png", "metrics.json"):
        api.upload_file(path_or_fileobj=str(KERNEL / name), path_in_repo=name, repo_id=repo_id)
    print(f"https://huggingface.co/{repo_id}")


if __name__ == "__main__":
    main()
