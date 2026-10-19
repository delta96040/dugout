"""Kaggle round trip for the writer fine-tune: upload data, push a GPU kernel, fetch its outputs.

Two jobs:
  teach  tools/finetune/teach.py: the teacher (Qwen3.8-27B, llama.cpp) answers data/finetune/prompts.jsonl
  train  tools/finetune/train.py: LoRA on data/finetune/{train,val}.jsonl

    uv run python tools/finetune/push.py data   --job teach|train      # inputs -> a private Kaggle dataset
    uv run python tools/finetune/push.py kernel --job teach|train [--smoke] [--private] [--base M]
    uv run python tools/finetune/push.py status --job ...
    uv run python tools/finetune/push.py output --job ...              # -> data/finetune/kaggle/<kernel>/

Credentials: KAGGLE_USERNAME + KAGGLE_API_TOKEN (or the legacy KAGGLE_KEY) from .env or the environment; never
printed. The Kaggle CLI runs pinned through `uvx`, not as a workspace dependency. Datasets stay private (teacher
notes quote headlines); notebooks are public unless --private.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "finetune"
KAGGLE = ["uvx", "--from", "kaggle==2.2.4", "kaggle"]
BUILD = ROOT / ".cache" / "kaggle"
CRED_KEYS = ("KAGGLE_USERNAME", "KAGGLE_API_TOKEN", "KAGGLE_KEY")
# job -> (dataset slug, files in it, kernel script)
JOBS = {
    "train": ("dugout-writer-sft", ("train.jsonl", "val.jsonl", "stats.json"), "train.py"),
    "teach": ("dugout-writer-prompts", ("prompts.jsonl",), "teach.py"),
}


def env() -> dict[str, str]:
    out = dict(os.environ)
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in CRED_KEYS and key.strip() not in out:
                out[key.strip()] = value.strip().strip('"')
    if not out.get("KAGGLE_USERNAME") or not (out.get("KAGGLE_API_TOKEN") or out.get("KAGGLE_KEY")):
        raise SystemExit("KAGGLE_USERNAME / KAGGLE_API_TOKEN missing (add them to .env)")
    return out


def kaggle(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*KAGGLE, *args], env=env(), check=check, text=True)


def kernel_slug(job: str, base: str) -> str:
    if job == "teach":
        return "dugout-writer-teacher"
    size = base.rsplit("-", 2)[-2].lower() if "-" in base else "model"  # Qwen2.5-1.5B-Instruct -> 1.5b
    return f"dugout-writer-lora-{size.replace('.', '-')}"


def push_data(job: str) -> None:
    user = env()["KAGGLE_USERNAME"]
    slug, files, _ = JOBS[job]
    d = BUILD / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    for name in files:
        shutil.copy(DATA / name, d / name)
    meta = {"title": slug, "id": f"{user}/{slug}", "licenses": [{"name": "other"}]}
    (d / "dataset-metadata.json").write_text(json.dumps(meta, indent=1))
    exists = kaggle("datasets", "status", f"{user}/{slug}", check=False).returncode == 0
    if exists:
        kaggle("datasets", "version", "-p", str(d), "-m", "refresh", "-r", "zip")
    else:
        kaggle("datasets", "create", "-p", str(d), "-r", "zip")  # private unless --public


def push_kernel(job: str, base: str, smoke: bool = False, public: bool = False) -> None:
    user = env()["KAGGLE_USERNAME"]
    dataset, _, script = JOBS[job]
    slug = kernel_slug(job, base)
    d = BUILD / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    code = (ROOT / "tools" / "finetune" / script).read_text(encoding="utf-8")
    code = code.replace('os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")', repr(base))
    if smoke:  # a slice: checks installs and every step before the real run
        code = code.replace("SMOKE = False", "SMOKE = True")
    (d / script).write_text(code, encoding="utf-8")
    meta = {
        "id": f"{user}/{slug}",
        "title": slug,
        "code_file": script,
        "language": "python",
        "kernel_type": "script",
        "is_private": not public,  # public notebooks (owner's choice; no secrets in them); datasets stay private
        "enable_gpu": True,
        "enable_internet": True,  # pip pins, models from the Hub, llama.cpp releases
        "machine_shape": "NvidiaTeslaT4",  # "T4 x2": the teacher splits its layers over both
        "dataset_sources": [f"{user}/{dataset}"],
        "competition_sources": [],
        "kernel_sources": [],
    }
    (d / "kernel-metadata.json").write_text(json.dumps(meta, indent=1))
    kaggle("kernels", "push", "-p", str(d))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["data", "kernel", "status", "output"])
    p.add_argument("--job", choices=list(JOBS), default="train")
    p.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    p.add_argument("--smoke", action="store_true", help="tiny run to test the kernel end to end")
    p.add_argument("--private", action="store_true", help="keep the notebook private (default public)")
    a = p.parse_args()
    user = env()["KAGGLE_USERNAME"]
    slug = kernel_slug(a.job, a.base)
    if a.what == "data":
        push_data(a.job)
    elif a.what == "kernel":
        push_kernel(a.job, a.base, a.smoke, public=not a.private)
    elif a.what == "status":
        kaggle("kernels", "status", f"{user}/{slug}")
    else:
        out = DATA / "kaggle" / slug
        out.mkdir(parents=True, exist_ok=True)
        kaggle("kernels", "output", f"{user}/{slug}", "-p", str(out))


if __name__ == "__main__":
    main()
