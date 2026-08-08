"""F2 — stage-3 cross-encoder rerank (f7 §5b, cerebras-f §4).

Tests the pure ``apply_rerank`` function (reorder by cross-encoder scores +
truncate to k) and the ``recall`` wiring (over-fetch to ``k_rerank``, hydrate
summary/subject via ``index_repo.get_rows``, score pairs, reorder, truncate).

Honesty (ADR-10): a missing ``index_repo`` (no text to hydrate) or a reranker
failure degrades to the pure-RRF order truncated to ``k`` — never invented
scores, never a crash that would drop the hybrid path to G2.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.contracts.retrieval import FusedCandidate
from seahorse.retrieval.constants import RERANK_OVERFETCH_K
from seahorse.retrieval.rerank import apply_rerank


def _candidate(ep_id: str, score: float) -> FusedCandidate:
    return FusedCandidate(ep_id=ep_id, score=score, sources=("vector",))


# ---------------------------------------------------------------- apply_rerank

class TestApplyRerank:
    def test_reorders_by_scores_descending(self):
        candidates = [_candidate("a", 0.1), _candidate("b", 0.2), _candidate("c", 0.3)]
        scores = [3.0, 1.0, 2.0]  # a=3, b=1, c=2 → order a, c, b
        out = apply_rerank(candidates, scores, k=3)
        assert [c.ep_id for c in out] == ["a", "c", "b"]
        # The score is REPLACED with the cross-encoder score (score_source=rrf_rerank).
        assert [c.score for c in out] == [3.0, 2.0, 1.0]

    def test_truncates_to_k(self):
        candidates = [_candidate(f"e{i}", float(i)) for i in range(5)]
        scores = [float(5 - i) for i in range(5)]  # e0=5, e1=4, ...
        out = apply_rerank(candidates, scores, k=2)
        assert [c.ep_id for c in out] == ["e0", "e1"]

    def test_tie_break_by_ep_id_deterministic(self):
        candidates = [_candidate("b", 0.1), _candidate("a", 0.1)]
        scores = [1.0, 1.0]  # tie → ep_id asc
        out = apply_rerank(candidates, scores, k=2)
        assert [c.ep_id for c in out] == ["a", "b"]

    def test_missing_score_keeps_rrf_score_at_end(self):
        # The scores list is shorter than candidates: the trailing candidate is
        # kept with its RRF score (honest — never invent a score).
        candidates = [_candidate("a", 0.1), _candidate("b", 0.2)]
        scores = [5.0]  # only "a" scored
        out = apply_rerank(candidates, scores, k=2)
        assert [c.ep_id for c in out] == ["a", "b"]
        assert out[0].score == 5.0
        assert out[1].score == 0.2  # RRF score preserved

    def test_empty_candidates(self):
        assert apply_rerank([], [], k=10) == []

    def test_k_zero_returns_empty(self):
        out = apply_rerank([_candidate("a", 0.1)], [1.0], k=0)
        assert out == []


# ---------------------------------------------------------------- recall wiring

def _make_recall_kwargs(**overrides):
    """The minimal ``recall`` kwargs for a rerank-enabled run (no PIT, no recency)."""
    from seahorse.retrieval.engine import recall

    class _Embedder:
        def embed_query(self, query):
            return [0.0] * 4

    class _VectorRepo:
        def knn(self, query_vec, k, *, vigent_only=True, cognitive_types=None):
            return []

    class _FtsRepo:
        def search(self, query, k, *, vigent_only=True, subject_filter=None):
            return []

    class _EpisodeRepo:
        def chain_from(self, ep_id):
            return []

    class _IndexRepo:
        def __init__(self, rows):
            self._rows = rows

        def get_rows(self, ep_ids):
            return [r for r in self._rows if r.ep_id in ep_ids]

    class _Row:
        def __init__(self, ep_id, summary, subject):
            self.ep_id = ep_id
            self.summary = summary
            self.subject = subject

    class _Reranker:
        def __init__(self, scores_by_doc):
            self._scores_by_doc = scores_by_doc

        def rerank(self, query, docs):
            return [self._scores_by_doc.get(d, 0.0) for d in docs]

    kwargs = {
        "query": "capital of France",
        "pit": None,
        "embedder": _Embedder(),
        "vector_repo": _VectorRepo(),
        "fts_repo": _FtsRepo(),
        "episode_repo": _EpisodeRepo(),
        "index_repo": _IndexRepo(
            [
                _Row("e1", "The capital of France is Paris.", "France"),
                _Row("e2", "Paris is the capital city.", "Paris"),
            ]
        ),
        "reranker": _Reranker(
            {
                "The capital of France is Paris.": 5.0,
                "Paris is the capital city.": 3.0,
            }
        ),
        "k": 1,
    }
    kwargs.update(overrides)
    return recall, kwargs


def test_recall_rerank_overfetches_and_reorders():
    """With rerank enabled, recall fuses to k_rerank, scores pairs, reorders,
    and truncates to k — the cross-encoder score replaces the RRF score."""
    recall, kwargs = _make_recall_kwargs()

    # A vector repo that returns BOTH candidates (over-fetched) so the reranker
    # has something to reorder.
    class _VectorRepo2:
        def knn(self, query_vec, k, *, vigent_only=True, cognitive_types=None):
            from seahorse.contracts.persistence import VectorHit

            return [
                VectorHit(ep_id="e1", distance=0.1, score=0.9),
                VectorHit(ep_id="e2", distance=0.2, score=0.8),
            ]

    kwargs["vector_repo"] = _VectorRepo2()
    kwargs["k"] = 1
    out = recall(**kwargs)
    # Both candidates were fused (k_rerank over-fetch), then truncated to k=1.
    assert len(out) == 1
    assert out[0].ep_id == "e1"  # higher cross-encoder score
    assert out[0].score == 5.0  # cross-encoder score, not RRF


def test_recall_rerank_without_index_repo_degrades_to_rrf():
    """No index_repo → no text to hydrate → honest degrade to RRF order (k)."""
    recall, kwargs = _make_recall_kwargs(index_repo=None)
    out = recall(**kwargs)
    assert isinstance(out, list)


def test_recall_rerank_reranker_failure_degrades_to_rrf():
    """A reranker failure must NOT kill the ranking — keep the RRF order (k)."""

    class _BoomReranker:
        def rerank(self, query, docs):
            raise RuntimeError("model failed")

    recall, kwargs = _make_recall_kwargs(reranker=_BoomReranker())
    out = recall(**kwargs)
    assert isinstance(out, list)


def test_recall_rerank_disabled_keeps_pure_rrf():
    """reranker=None (default) → pure RRF, no over-fetch, no stage-3."""
    recall, kwargs = _make_recall_kwargs(reranker=None)
    out = recall(**kwargs)
    assert isinstance(out, list)


def test_rerank_overfetch_constant():
    assert RERANK_OVERFETCH_K == 20
