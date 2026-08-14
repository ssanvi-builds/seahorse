"""``QueryReranker`` Protocol extension point.

The extension point is owned by hybrid retrieval (the component that needs it)
and materialized by the embeddings extra (fastembed ``TextCrossEncoder``). These
tests pin:
- the Protocol surface (``rerank(query, docs) -> Sequence[float]``);
- a conforming impl satisfies the Protocol; a non-conforming one does not;
- the scores are aligned with the input docs (one score per doc).
"""

from __future__ import annotations

from collections.abc import Sequence

from seahorse.contracts.rerank import QueryReranker


class _ConformingReranker:
    """Post-extension-point shape: ``rerank(query, docs) -> Sequence[float]``."""

    def rerank(self, query: str, docs: Sequence[str]) -> Sequence[float]:
        return [float(len(doc)) for doc in docs]


class _NonConformingReranker:
    """Wrong shape: no ``rerank`` method. Must NOT satisfy the Protocol."""

    def score(self, query: str, doc: str) -> float:  # pragma: no cover - shape probe
        return 0.0


def test_conforming_reranker_satisfies_protocol() -> None:
    assert isinstance(_ConformingReranker(), QueryReranker)


def test_non_conforming_reranker_does_not_satisfy_protocol() -> None:
    assert not isinstance(_NonConformingReranker(), QueryReranker)


def test_rerank_returns_one_score_per_doc() -> None:
    reranker = _ConformingReranker()
    docs = ["a", "bb", "ccc"]
    scores = reranker.rerank("query", docs)
    assert len(scores) == len(docs)
    assert all(isinstance(s, float) for s in scores)


def test_rerank_scores_are_aligned_with_docs() -> None:
    reranker = _ConformingReranker()
    docs = ["a", "bb", "ccc"]
    scores = list(reranker.rerank("query", docs))
    # The conforming impl scores by doc length — alignment is positional.
    assert scores == [1.0, 2.0, 3.0]
