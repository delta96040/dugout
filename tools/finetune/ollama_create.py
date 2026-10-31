"""Serve the fine-tuned writer (and its base, for a fair comparison) through local Ollama.

    uv run python tools/finetune/ollama_create.py <dugout-writer-q4_K_M.gguf> [--name dugout-writer-1.5b]

Creates two models with the same chat template (Qwen2.5's, copied from `qwen2.5:1.5b`), the same 8k context (a
scout prompt is ~2.5k tokens) and the same q4_K_M quantization:
  - `<name>`: the merged fine-tune, the q4_K_M GGUF the Kaggle kernel exports
  - `qwen2.5-1.5b-8k`: the untouched base model with the same context
Then `dugout-agents bench-writer --models local:<name>,local:qwen2.5-1.5b-8k --clubs heldout`.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

BASE = "qwen2.5:1.5b"
CTX = 8192


def modelfile(source: str) -> str:
    """Base Modelfile with FROM replaced and the context set; the template and stop tokens are Qwen2.5's."""
    text = subprocess.run(
        ["ollama", "show", BASE, "--modelfile"], check=True, capture_output=True, text=True
    ).stdout
    lines = [ln for ln in text.splitlines() if not ln.startswith(("FROM ", "#")) and "num_ctx" not in ln]
    return "\n".join([f"FROM {source}", *lines, f"PARAMETER num_ctx {CTX}"]) + "\n"


def create(name: str, source: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        mf = Path(d) / "Modelfile"
        mf.write_text(modelfile(source), encoding="utf-8")
        subprocess.run(["ollama", "create", name, "-f", str(mf)], check=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("gguf", type=Path)
    p.add_argument("--name", default="dugout-writer-1.5b")
    a = p.parse_args()
    subprocess.run(["ollama", "pull", BASE], check=True)
    create("qwen2.5-1.5b-8k", BASE)  # Ollama's qwen2.5:1.5b is q4_K_M too
    create(a.name, str(a.gguf.resolve()))


if __name__ == "__main__":
    main()
