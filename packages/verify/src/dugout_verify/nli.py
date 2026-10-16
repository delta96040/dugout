"""Fact-Check models: an NLI cross-encoder (DeBERTa-v3) and a dense retriever (BGE).

Weights are loaded from the Hugging Face Hub at pinned revisions, safetensors only, never with remote code.
Both are behind small protocols so tests can use deterministic fakes (no network, no torch).
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class HubModel:
    repo: str
    revision: str  # commit sha: a moved tag can never silently change the weights we run
    licence: str


NLI_MODELS = {
    "base": HubModel(
        "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli", "6f5cf0a2b59cabb106aca4c287eed12e357e90eb", "MIT"
    ),
    "large": HubModel(
        "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli",
        "b3546ea6b0346eb6f8d5d68b13c7dc6d0376b3d7",
        "MIT",
    ),
}
RETRIEVERS = {
    "bge-small": HubModel("BAAI/bge-small-en-v1.5", "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a", "MIT"),
}
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class NLI(Protocol):
    def scores(self, premises: list[str], hypothesis: str) -> np.ndarray:
        """(n, 3) probabilities: entailment, neutral, contradiction for each premise."""
        ...


class Embedder(Protocol):
    def embed(self, texts: list[str], query: bool = False) -> np.ndarray:
        """(n, d) L2-normalised embeddings."""
        ...


class DebertaNLI:
    def __init__(self, size: str = "base", max_length: int = 512, batch: int = 8):
        self.spec, self.max_length, self.batch = NLI_MODELS[size], max_length, batch

    @cached_property
    def _model(self):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tok = AutoTokenizer.from_pretrained(self.spec.repo, revision=self.spec.revision)
        model = AutoModelForSequenceClassification.from_pretrained(
            self.spec.repo, revision=self.spec.revision, use_safetensors=True
        ).eval()
        labels = {v.lower(): int(k) for k, v in model.config.id2label.items()}
        order = [labels["entailment"], labels["neutral"], labels["contradiction"]]
        return tok, model, order

    def scores(self, premises: list[str], hypothesis: str) -> np.ndarray:
        import torch

        tok, model, order = self._model
        out = []
        for i in range(0, len(premises), self.batch):
            enc = tok(
                premises[i : i + self.batch],
                [hypothesis] * len(premises[i : i + self.batch]),
                truncation="only_first",
                max_length=self.max_length,
                padding=True,
                return_tensors="pt",
            )
            with torch.inference_mode():  # per call: grad mode is thread-local (LangGraph uses threads)
                logits = model(**enc).logits
            out.append(torch.softmax(logits, -1)[:, order].numpy())
        return np.vstack(out) if out else np.zeros((0, 3))


class BgeEmbedder:
    def __init__(self, name: str = "bge-small", batch: int = 32):
        self.spec, self.batch = RETRIEVERS[name], batch

    @cached_property
    def _model(self):
        from transformers import AutoModel, AutoTokenizer

        tok = AutoTokenizer.from_pretrained(self.spec.repo, revision=self.spec.revision)
        model = AutoModel.from_pretrained(
            self.spec.repo, revision=self.spec.revision, use_safetensors=True
        ).eval()
        return tok, model

    def embed(self, texts: list[str], query: bool = False) -> np.ndarray:
        import torch

        tok, model = self._model
        if query:
            texts = [BGE_QUERY_PREFIX + t for t in texts]
        out = []
        for i in range(0, len(texts), self.batch):
            enc = tok(
                texts[i : i + self.batch], truncation=True, max_length=512, padding=True, return_tensors="pt"
            )
            with torch.inference_mode():
                cls = model(**enc).last_hidden_state[:, 0].numpy()  # BGE uses the [CLS] vector
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
        return np.vstack(out) if out else np.zeros((0, 384))


class _Store:
    """A small SQLite key -> bytes store, safe across threads. Entries unused for `keep_days` are dropped on open."""

    def __init__(self, path: Path, keep_days: int = 30):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._today = date.today().toordinal()
        with self._lock, self._con:
            self._con.execute(
                "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v BLOB NOT NULL, seen INTEGER)"
            )
            self._con.execute("DELETE FROM kv WHERE seen < ?", [self._today - keep_days])

    def get_many(self, keys: list[str]) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        with self._lock:
            for i in range(0, len(keys), 500):
                part = keys[i : i + 500]
                q = f"SELECT k, v FROM kv WHERE k IN ({','.join('?' * len(part))})"
                out |= dict(self._con.execute(q, part).fetchall())
            if out:
                with self._con:
                    self._con.executemany(
                        "UPDATE kv SET seen = ? WHERE k = ?", [(self._today, k) for k in out]
                    )
        return out

    def put_many(self, items: dict[str, bytes]) -> None:
        with self._lock, self._con:
            self._con.executemany(
                "INSERT OR REPLACE INTO kv VALUES (?, ?, ?)", [(k, v, self._today) for k, v in items.items()]
            )


def _key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class CachedNLI:
    """NLI scores kept on disk between runs. The weights are pinned, so the same premise and hypothesis always
    score the same: most of a night's Fact-Check pairs were already scored the night before."""

    def __init__(self, inner: DebertaNLI, path: Path):
        self.inner, self.store = inner, _Store(path)
        self._tag = f"nli:{inner.spec.revision}:{inner.max_length}"

    def scores(self, premises: list[str], hypothesis: str) -> np.ndarray:
        keys = [_key(self._tag, p, hypothesis) for p in premises]
        hit = self.store.get_many(keys)
        miss = [i for i, k in enumerate(keys) if k not in hit]
        if miss:
            fresh = self.inner.scores([premises[i] for i in miss], hypothesis).astype(np.float32)
            new = {keys[i]: row.tobytes() for i, row in zip(miss, fresh, strict=True)}
            self.store.put_many(new)
            hit |= new
        if not keys:
            return np.zeros((0, 3), dtype=np.float32)
        return np.vstack([np.frombuffer(hit[k], dtype=np.float32) for k in keys])


class CachedEmbedder:
    """Embeddings kept on disk between runs (pinned weights: a text always embeds the same)."""

    def __init__(self, inner: BgeEmbedder, path: Path):
        self.inner, self.store = inner, _Store(path)
        self._tag = f"emb:{inner.spec.revision}"

    def embed(self, texts: list[str], query: bool = False) -> np.ndarray:
        keys = [_key(self._tag, "q" if query else "p", t) for t in texts]
        hit = self.store.get_many(list(dict.fromkeys(keys)))
        miss = list(dict.fromkeys(i for i, k in enumerate(keys) if k not in hit))
        if miss:
            fresh = self.inner.embed([texts[i] for i in miss], query=query).astype(np.float32)
            new = {keys[i]: row.tobytes() for i, row in zip(miss, fresh, strict=True)}
            self.store.put_many(new)
            hit |= new
        if not keys:
            return np.zeros((0, 384), dtype=np.float32)
        return np.vstack([np.frombuffer(hit[k], dtype=np.float32) for k in keys])
