"""Synthetic corpus for the F7 experiments — mechanical verification in CI.

The experiments must be runnable WITHOUT the heavy stack (no HuggingFace, no
Ollama, no model download): ``make_synthetic_dataset`` is the deterministic
canonical corpus (5 instances covering the LMEB question types the experiments
slice on), and ``HashEmbedder`` is a deterministic content-hash passage
embedder that keeps the vec0 kNN path REAL without any model (semantically
overlapping texts share buckets, so the hybrid regime behaves plausibly).

Honesty (ADR-10): synthetic results verify the harness MECHANICS, not the
science — the authoritative F1/F3 decision comes from an LMEB-S run
(``--corpus lmeb-s``).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance
from seahorse.embeddings.types import ModelIdentity


def _session(session_id: str, date: datetime, turns: list[dict]) -> dict:
    return {"session_id": session_id, "date": date, "turns": turns}


def make_synthetic_dataset() -> BenchmarkDataset:
    """The deterministic canonical test corpus (5 LMEB capabilities, f5-16 §4.1).

    Same shape as ``longmemeval-cleaned`` rows after ``_from_row``: one
    single-session-user, one knowledge-update, one multi-session, one
    temporal-reasoning, one abstention. The question_type/cognitive_category
    slices the recency/embed experiments stratify on are all present.
    """
    d1 = datetime(2026, 1, 1, tzinfo=UTC)
    d2 = datetime(2026, 1, 2, tzinfo=UTC)
    d3 = datetime(2026, 1, 3, tzinfo=UTC)
    instances = (
        BenchmarkInstance(
            instance_id="q1",
            question="What is the capital of France?",
            golden_answer="Paris",
            golden_session_ids=("s1",),
            golden_evidence=(),
            question_type="single-session-user",
            capabilities=("information-extraction",),
            cognitive_category="episodic",
            question_date=None,
            haystack=(
                _session(
                    "s1",
                    d1,
                    [
                        {
                            "body": "# France\n\nThe capital of France is Paris.",
                            "title": "France",
                            "fact_key": "france-capital",
                        }
                    ],
                ),
            ),
        ),
        BenchmarkInstance(
            instance_id="q2",
            question="What is the new capital of France?",
            golden_answer="Lyon",
            golden_session_ids=("s2",),
            golden_evidence=(),
            question_type="knowledge-update",
            capabilities=("knowledge-update",),
            cognitive_category="semantic",
            question_date=None,
            haystack=(
                _session(
                    "s1",
                    d1,
                    [
                        {
                            "body": "# France\n\nThe capital of France is Paris.",
                            "title": "France",
                            "fact_key": "france-capital",
                        }
                    ],
                ),
                _session(
                    "s2",
                    d2,
                    [
                        {
                            "body": "# France\n\nThe capital of France is now Lyon.",
                            "title": "France",
                            "fact_key": "france-capital",
                        }
                    ],
                ),
            ),
            knowledge_updates=(
                {
                    "fact_key": "france-capital",
                    "old_ep_id": None,
                    "old_body": "# France\n\nThe capital of France is Paris.",
                    "new_body": "# France\n\nThe capital of France is now Lyon.",
                    "session_id": "s2",
                    "date": d2,
                },
            ),
        ),
        BenchmarkInstance(
            instance_id="q3",
            question="What did Alice say about the project on day 3?",
            golden_answer="It is on track.",
            golden_session_ids=("s3",),
            golden_evidence=(),
            question_type="multi-session",
            capabilities=("multi-session-reasoning",),
            cognitive_category="semantic",
            question_date=None,
            haystack=(
                _session(
                    "s3",
                    d3,
                    [
                        {
                            "body": "# Project\n\nAlice said the project is on track.",
                            "title": "Project",
                        }
                    ],
                ),
            ),
        ),
        BenchmarkInstance(
            instance_id="q4",
            question="What was the capital before the change?",
            golden_answer="Paris",
            golden_session_ids=("s1",),
            golden_evidence=(),
            question_type="temporal-reasoning",
            capabilities=("temporal-reasoning",),
            cognitive_category="semantic",
            question_date=d1,
            haystack=(
                _session(
                    "s1",
                    d1,
                    [
                        {
                            "body": "# France\n\nThe capital of France is Paris.",
                            "title": "France",
                            "fact_key": "france-capital",
                        }
                    ],
                ),
                _session(
                    "s2",
                    d2,
                    [
                        {
                            "body": "# France\n\nThe capital of France is now Lyon.",
                            "title": "France",
                            "fact_key": "france-capital",
                        }
                    ],
                ),
            ),
        ),
        BenchmarkInstance(
            instance_id="q5",
            question="Is there any information about the weather?",
            golden_answer="No",
            golden_session_ids=(),
            golden_evidence=(),
            question_type="abstention",
            capabilities=("abstention",),
            cognitive_category="n/a",
            question_date=None,
            haystack=(),
            abstention=True,
        ),
    )
    return BenchmarkDataset(
        name="synthetic",
        version="1.0.0",
        config="s",
        split_hash="abc123",
        loader_code_sha256="def456",
        instances=instances,
        metadata={"total_questions": len(instances)},
    )


class HashEmbedder:
    """Deterministic content-hash passage embedder (synthetic experiment, f7 §5).

    Maps each text to a sparse token-hash vector (lower-cased tokens → SHA-256
    bucket), L2-normalized. Semantically overlapping texts share buckets so the
    vec0 kNN path behaves realistically without any model download, determinism
    is bit-stable across runs/processes (pure stdlib hashing), and numpy stays
    out of the module import path (lazy inside ``embed``).
    """

    dim = 384

    async def embed(self, texts, role):
        import numpy as np

        vecs = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in text.lower().split():
                bucket = int.from_bytes(
                    hashlib.sha256(token.encode("utf-8")).digest()[:4], "big"
                ) % self.dim
                vecs[i, bucket] += 1.0
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    def model_identity(self) -> ModelIdentity:
        return ModelIdentity(
            backend="test",
            model_name="hash",
            revision="r",
            dim=self.dim,
            quantization="fp32",
            normalized=True,
        )


class HashReranker:
    """Deterministic content-overlap reranker (synthetic experiment, f7 §5b).

    Scores each doc by the number of query tokens it shares (higher = more
    relevant), so the stage-3 reorder behaves plausibly without any model
    download. Tokens are normalized (lower-cased, punctuation stripped) so
    "france?" matches "france" — the real cross-encoder's tokenizer handles
    this; the stub must too or the synthetic verification would score 0.0.
    Determinism is bit-stable across runs/processes (pure stdlib). Verifies the
    harness MECHANICS — NOT the science (ADR-10).
    """

    def rerank(self, query: str, docs: Sequence[str]) -> Sequence[float]:
        q_tokens = set(_normalize_tokens(query))
        return [
            float(sum(1 for t in _normalize_tokens(doc) if t in q_tokens))
            for doc in docs
        ]


def _normalize_tokens(text: str) -> list[str]:
    """Lower-case + strip non-alphanumeric tokens (HashReranker tokenizer)."""
    return [t for t in re.sub(r"[^a-z0-9 ]", "", text.lower()).split() if t]


__all__ = ["make_synthetic_dataset", "HashEmbedder", "HashReranker"]
