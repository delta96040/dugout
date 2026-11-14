"""Offline teacher on Kaggle GPUs: the same open weights as Groq's `qwen/qwen3.8-27b`, served by llama.cpp.

Groq's free tier caps that model at 200k tokens a day (about 80 dossier notes), too few for a training set. This
kernel serves Qwen3.8-27B (Apache-2.0; ggml-org's Q4_K_M GGUF, pinned revision) with a pinned llama.cpp CUDA
release split across Kaggle's two T4s, and answers every prompt in prompts.jsonl (exported by
`dugout-agents finetune-data --export-prompts`) with the production writer's settings: sample 0 at temperature
0.2 (as `OpenAICompatLLM`), sample 1 at 0.7, thinking off (Groq is called with reasoning_effort "none").
Nothing is judged here: `--import-replies` runs every sample through the writer checks and the publish gate locally.
Writes /kaggle/working/replies.jsonl (id, sample, temperature, content, seconds, tokens) and teach_metrics.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LLAMA_TAG = "b11330"  # 2026-10-02, after the GGUF's last conversion
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}"
ASSETS = [
    f"llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz",
    f"cudart-llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz",
]
MODEL = ("ggml-org/Qwen3.8-27B-GGUF", "Qwen3.8-27B-Q4_K_M.gguf", "71bc7b627595")  # repo, file, revision
SAMPLES = [(0, 0.2), (1, 0.7)]  # (sample, temperature)
SLOTS = 4  # parallel sequences in llama-server (continuous batching)
CTX_PER_SLOT = 6144  # a scout prompt is ~2.5k tokens, a note ~300
MAX_TOKENS = 600  # as OpenAICompatLLM
BUDGET_S = float(os.environ.get("TEACH_BUDGET_S", 10.5 * 3600))  # Kaggle stops GPU sessions at 12 h
OUT = Path("/kaggle/working")
SMOKE = False  # push.py teach --smoke: a handful of prompts


def sh(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def install_llama() -> Path:
    root = Path("/tmp/llama")
    root.mkdir(exist_ok=True)
    for name in ASSETS:
        path = root / name
        urllib.request.urlretrieve(f"{LLAMA_URL}/{name}", path)
        with tarfile.open(path) as tf:
            tf.extractall(root, filter="data")
    server = next(root.rglob("llama-server"))
    server.chmod(0o755)
    return server


def fetch_model() -> Path:
    sh(sys.executable, "-m", "pip", "install", "-q", "huggingface_hub==0.35.3")
    from huggingface_hub import hf_hub_download

    repo, name, rev = MODEL
    return Path(hf_hub_download(repo, name, revision=rev, local_dir="/tmp/model"))


def serve(server: Path, model: Path) -> subprocess.Popen:
    env = dict(os.environ, LD_LIBRARY_PATH=f"{server.parent}:{os.environ.get('LD_LIBRARY_PATH', '')}")
    cmd = [
        str(server), "-m", str(model), "--host", "127.0.0.1", "--port", "8080",
        "-ngl", "999", "--split-mode", "layer", "-np", str(SLOTS), "-c", str(SLOTS * CTX_PER_SLOT),
        "--jinja", "--reasoning-budget", "0", "--chat-template-kwargs", '{"enable_thinking": false}',
        "--no-webui",
    ]  # fmt: skip
    print("+", " ".join(cmd), flush=True)
    log = (OUT / "server.log").open("w")
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(900):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5) as r:
                if r.status == 200:
                    return proc
        except OSError:
            pass
        if proc.poll() is not None:
            raise SystemExit("llama-server exited; see server.log")
        time.sleep(2)
    raise SystemExit("llama-server did not become healthy")


def complete(messages: list[dict], temperature: float, seed: int) -> tuple[str, dict, float]:
    body = {"messages": messages, "temperature": temperature, "max_tokens": MAX_TOKENS, "seed": seed}
    req = urllib.request.Request(
        "http://127.0.0.1:8080/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        out = json.load(r)
    return out["choices"][0]["message"]["content"] or "", out.get("usage", {}), time.time() - t0


def main() -> None:
    started = time.time()
    data = next(Path("/kaggle/input").rglob("prompts.jsonl"))
    prompts = [json.loads(x) for x in data.read_text("utf-8").splitlines() if x.strip()]
    if SMOKE:
        prompts = prompts[:8]
    print(len(prompts), "prompts", flush=True)
    server = install_llama()
    model = fetch_model()
    proc = serve(server, model)
    ready = time.time()
    lock_path = OUT / "replies.jsonl"
    fh = lock_path.open("a", encoding="utf-8")
    # every prompt's sample 0 (production's temperature) before any sample 1: the time budget cuts sample 1 only
    jobs = [(p, s, t) for s, t in SAMPLES for p in prompts]
    tokens = {"prompt": 0, "completion": 0}
    done = 0

    def run(job):
        p, sample, temp = job
        if time.time() - started > BUDGET_S:
            return None
        content, usage, secs = complete(p["messages"], temp, 7 + sample)
        return {"id": p["id"], "sample": sample, "temperature": temp, "content": content,
                "seconds": round(secs, 1), "tokens": usage, "messages": p["messages"]}  # fmt: skip

    with ThreadPoolExecutor(SLOTS) as pool:
        for row in pool.map(run, jobs):
            if row is None:
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            done += 1
            tokens["prompt"] += row["tokens"].get("prompt_tokens", 0)
            tokens["completion"] += row["tokens"].get("completion_tokens", 0)
            if done % 50 == 0:
                rate = done / (time.time() - ready)
                print(f"{done}/{len(jobs)} samples, {rate * 3600:.0f}/h", flush=True)
    fh.close()
    proc.terminate()
    gen_s = time.time() - ready
    metrics = {
        "prompts": len(prompts),
        "samples_written": done,
        "samples_planned": len(jobs),
        "setup_seconds": round(ready - started),
        "generation_seconds": round(gen_s),
        "samples_per_hour": round(done / gen_s * 3600) if gen_s else None,
        "tokens": tokens,
        "model": MODEL,
        "llama_cpp": LLAMA_TAG,
        "slots": SLOTS,
        "samples": SAMPLES,
    }
    (OUT / "teach_metrics.json").write_text(json.dumps(metrics, indent=1))
    print(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
