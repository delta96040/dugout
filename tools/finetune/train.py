"""LoRA fine-tune of a small open-weight instruct model on dugout's writer data (Kaggle GPU kernel).

Runs as a Kaggle script kernel (`tools/finetune/push.py` uploads the data and this file). Input: the dataset
`<user>/dugout-writer-sft` (train.jsonl / val.jsonl from `dugout-agents finetune-data`, chat format). Loss is on
the assistant reply only. Writes to /kaggle/working: the LoRA adapter, the merged model as a GGUF (f16, for
`ollama create --quantize q4_K_M`), metrics.json (train/val loss before and after, timings, versions, seed).

Kaggle's T4 has no bf16: the base is loaded in fp16, LoRA weights train in fp32 under fp16 autocast.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PINS = [
    "transformers==4.56.2",
    "peft==0.17.1",
    "accelerate==1.10.1",
    "sentencepiece==0.2.1",
    "mistral-common==1.8.5",  # imported by convert_hf_to_gguf.py
]
LLAMA_CPP_TAG = "b6550"  # convert_hf_to_gguf.py only (pure Python), pinned

CONFIG = {
    "base_model": os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct"),
    "seed": 7,
    "epochs": 3,
    "lr": 2e-4,
    "batch_size": 2,
    "grad_accum": 8,
    "max_len": 4096,
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    "warmup_ratio": 0.05,
}
OUT = Path("/kaggle/working")


def sh(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def find_data() -> Path:
    for p in Path("/kaggle/input").rglob("train.jsonl"):
        return p.parent
    raise SystemExit("train.jsonl not found under /kaggle/input")


def main() -> None:
    sh(sys.executable, "-m", "pip", "install", "-q", *PINS)
    import torch
    import transformers
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments

    transformers.set_seed(CONFIG["seed"])
    data = find_data()
    rows = {
        s: [json.loads(x) for x in (data / f"{s}.jsonl").read_text("utf-8").splitlines() if x.strip()]
        for s in ("train", "val")
    }
    print({k: len(v) for k, v in rows.items()}, flush=True)

    tok = AutoTokenizer.from_pretrained(CONFIG["base_model"])
    tok.padding_side = "right"

    def encode(row: dict) -> dict | None:
        msgs = row["messages"]
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
        full = prompt + msgs[-1]["content"] + tok.eos_token  # <|im_end|> for Qwen chat models
        p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
        ids = tok(full, add_special_tokens=False)["input_ids"]
        if len(ids) > CONFIG["max_len"]:
            return None
        return {"input_ids": ids, "labels": [-100] * len(p_ids) + ids[len(p_ids) :]}

    enc = {s: [e for e in map(encode, r) if e] for s, r in rows.items()}
    print({s: (len(e), max(len(x["input_ids"]) for x in e)) for s, e in enc.items()}, flush=True)

    def collate(batch: list[dict]) -> dict:
        n = max(len(b["input_ids"]) for b in batch)
        pad = tok.pad_token_id
        return {
            "input_ids": torch.tensor([b["input_ids"] + [pad] * (n - len(b["input_ids"])) for b in batch]),
            "labels": torch.tensor([b["labels"] + [-100] * (n - len(b["labels"])) for b in batch]),
            "attention_mask": torch.tensor(
                [[1] * len(b["input_ids"]) + [0] * (n - len(b["input_ids"])) for b in batch]
            ),
        }

    model = AutoModelForCausalLM.from_pretrained(
        CONFIG["base_model"], torch_dtype=torch.float16, device_map={"": 0}
    )
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    lora = LoraConfig(
        r=CONFIG["lora_r"],
        lora_alpha=CONFIG["lora_alpha"],
        lora_dropout=CONFIG["lora_dropout"],
        target_modules=CONFIG["target_modules"],
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)
    for p in model.parameters():  # trainable LoRA weights in fp32 (fp16 master weights do not train stably)
        if p.requires_grad:
            p.data = p.data.float()
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=str(OUT / "checkpoints"),
        num_train_epochs=CONFIG["epochs"],
        learning_rate=CONFIG["lr"],
        per_device_train_batch_size=CONFIG["batch_size"],
        per_device_eval_batch_size=CONFIG["batch_size"],
        gradient_accumulation_steps=CONFIG["grad_accum"],
        warmup_ratio=CONFIG["warmup_ratio"],
        lr_scheduler_type="cosine",
        fp16=True,
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="no",
        report_to=[],
        seed=CONFIG["seed"],
        data_seed=CONFIG["seed"],
        group_by_length=True,
        remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model, args=args, train_dataset=enc["train"], eval_dataset=enc["val"], data_collator=collate
    )
    t0 = time.time()
    before = trainer.evaluate()["eval_loss"]
    trainer.train()
    after = trainer.evaluate()["eval_loss"]
    train_s = time.time() - t0

    adapter = OUT / "adapter"
    model.save_pretrained(adapter)
    tok.save_pretrained(adapter)
    merged = OUT / "merged"
    model = model.merge_and_unload()
    model = model.half()
    model.save_pretrained(merged, safe_serialization=True)
    tok.save_pretrained(merged)

    sh(
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        LLAMA_CPP_TAG,
        "https://github.com/ggml-org/llama.cpp",
        "/tmp/llama.cpp",
    )
    sh(sys.executable, "-m", "pip", "install", "-q", "/tmp/llama.cpp/gguf-py")  # the gguf package of that tag
    gguf = OUT / "dugout-writer-f16.gguf"
    sh(
        sys.executable,
        "/tmp/llama.cpp/convert_hf_to_gguf.py",
        str(merged),
        "--outtype",
        "f16",
        "--outfile",
        str(gguf),
    )
    sh("rm", "-rf", str(merged), str(OUT / "checkpoints"))  # keep the download small: adapter + GGUF

    metrics = {
        "config": CONFIG,
        "examples": {s: len(e) for s, e in enc.items()},
        "val_loss_base": before,
        "val_loss_finetuned": after,
        "train_seconds": round(train_s),
        "log_history": trainer.state.log_history,
        "gpu": torch.cuda.get_device_name(0),
        "versions": {"torch": torch.__version__, "pins": PINS, "llama.cpp": LLAMA_CPP_TAG},
    }
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=1))
    print(json.dumps({k: v for k, v in metrics.items() if k != "log_history"}, indent=1))


if __name__ == "__main__":
    main()
