from types import SimpleNamespace

import numpy as np
from dugout_verify import nli as nli_mod
from dugout_verify.nli import CachedEmbedder, CachedNLI


class CountingNLI:
    spec = SimpleNamespace(revision="r1")
    max_length = 512

    def __init__(self):
        self.seen: list[str] = []

    def scores(self, premises, hypothesis):
        self.seen += premises
        return np.array([[len(p) / 100, 0.5, 0.25] for p in premises], dtype=np.float32)


class CountingEmbedder:
    spec = SimpleNamespace(revision="r1")

    def __init__(self):
        self.seen: list[tuple[str, bool]] = []

    def embed(self, texts, query=False):
        self.seen += [(t, query) for t in texts]
        v = np.array([[len(t), 1.0 if query else 0.0, 1.0] for t in texts], dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_nli_scores_are_reused_across_runs_and_match(tmp_path):
    inner = CountingNLI()
    first = CachedNLI(inner, tmp_path / "nli.sqlite").scores(["a", "bb"], "h")
    again = CachedNLI(inner, tmp_path / "nli.sqlite")  # a new run, same file
    out = again.scores(["bb", "ccc", "a"], "h")
    assert inner.seen == ["a", "bb", "ccc"]  # only the new premise was scored
    np.testing.assert_array_equal(out[[2, 0]], first)
    assert again.scores(["a"], "other hypothesis").shape == (1, 3) and inner.seen[-1] == "a"


def test_embeddings_keep_query_and_passage_apart(tmp_path):
    inner = CountingEmbedder()
    emb = CachedEmbedder(inner, tmp_path / "e.sqlite")
    p = emb.embed(["x", "yy"])
    q = emb.embed(["x"], query=True)
    assert not np.allclose(p[0], q[0])
    np.testing.assert_array_equal(emb.embed(["yy", "x"]), p[[1, 0]])
    assert inner.seen == [("x", False), ("yy", False), ("x", True)]
    assert emb.embed([]).shape[0] == 0


def test_entries_unused_for_a_month_are_dropped(tmp_path, monkeypatch):
    inner = CountingNLI()
    CachedNLI(inner, tmp_path / "n.sqlite").scores(["a"], "h")

    real = nli_mod.date

    class Later(real):
        @classmethod
        def today(cls):
            return real.fromordinal(real.today().toordinal() + 31)

    monkeypatch.setattr(nli_mod, "date", Later)
    CachedNLI(inner, tmp_path / "n.sqlite").scores(["a"], "h")
    assert inner.seen == ["a", "a"]
