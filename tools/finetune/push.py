"""Kaggle round trip for the writer fine-tune: upload data, push the training kernel, fetch its outputs.

    uv run python tools/finetune/push.py data                 # data/finetune/{train,val}.jsonl -> private dataset
    uv run python tools/finetune/push.py kernel [--base M]    # tools/finetune/train.py -> GPU kernel (T4)
    uv run python tools/finetune/push.py status
    uv run python tools/finetune/push.py output               # -> data/finetune/kaggle/<kernel>/

Credentials: KAGGLE_USERNAME / KAGGLE_KEY from .env (or the environment); never printed. The Kaggle CLI runs
pinned through `uvx`, not as a workspace dependency. The dataset stays private: teacher notes quote headlines.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KAGGLE = ["uvx", "--from", "kaggle==1.7.4.5", "kaggle"]
DATASET = "dugout-writer-sft"
BUILD = ROOT / ".cache" / "kaggle"


def env() -> dict[str, str]:
    out = dict(os.environ)
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in ("KAGGLE_USERNAME", "KAGGLE_KEY") and key.strip() not in out:
                out[key.strip()] = value.strip().strip('"')
    if not out.get("KAGGLE_USERNAME") or not out.get("KAGGLE_KEY"):
        raise SystemExit("KAGGLE_USERNAME / KAGGLE_KEY missing (add them to .env)")
    return out


def kaggle(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*KAGGLE, *args], env=env(), check=check, text=True)


def kernel_slug(base: str) -> str:
    size = base.rsplit("-", 2)[-2].lower() if "-" in base else "model"  # Qwen2.5-1.5B-Instruct -> 1.5b
    return f"dugout-writer-lora-{size.replace('.', '-')}"


def push_data() -> None:
    user = env()["KAGGLE_USERNAME"]
    d = BUILD / "data"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    for name in ("train.jsonl", "val.jsonl", "stats.json"):
        shutil.copy(ROOT / "data" / "finetune" / name, d / name)
    meta = {"title": DATASET, "id": f"{user}/{DATASET}", "licenses": [{"name": "other"}]}
    (d / "dataset-metadata.json").write_text(json.dumps(meta, indent=1))
    exists = kaggle("datasets", "status", f"{user}/{DATASET}", check=False).returncode == 0
    if exists:
        kaggle("datasets", "version", "-p", str(d), "-m", "refresh", "-r", "zip")
    else:
        kaggle("datasets", "create", "-p", str(d), "-r", "zip")  # private unless --public


def push_kernel(base: str) -> None:
    user = env()["KAGGLE_USERNAME"]
    slug = kernel_slug(base)
    d = BUILD / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    code = (ROOT / "tools" / "finetune" / "train.py").read_text(encoding="utf-8")
    code = code.replace('os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")', repr(base))
    (d / "train.py").write_text(code, encoding="utf-8")
    meta = {
        "id": f"{user}/{slug}",
        "title": slug,
        "code_file": "train.py",
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": True,  # pip pins, the base model from the Hub, llama.cpp's converter
        "machine_shape": "NvidiaTeslaT4",
        "dataset_sources": [f"{user}/{DATASET}"],
        "competition_sources": [],
        "kernel_sources": [],
    }
    (d / "kernel-metadata.json").write_text(json.dumps(meta, indent=1))
    kaggle("kernels", "push", "-p", str(d))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["data", "kernel", "status", "output"])
    p.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    a = p.parse_args()
    user = env()["KAGGLE_USERNAME"]
    slug = kernel_slug(a.base)
    if a.what == "data":
        push_data()
    elif a.what == "kernel":
        push_kernel(a.base)
    elif a.what == "status":
        kaggle("kernels", "status", f"{user}/{slug}")
    else:
        out = ROOT / "data" / "finetune" / "kaggle" / slug
        out.mkdir(parents=True, exist_ok=True)
        kaggle("kernels", "output", f"{user}/{slug}", "-p", str(out))


if __name__ == "__main__":
    main()
