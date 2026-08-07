"""Shared fixtures for the #16 benchmark skeleton tests.

The synthetic dataset is the canonical test corpus: no HuggingFace download in
CI (f5-16 §4.1 — tests use a synthetic dataset). The fakes (reader LLM, judge)
are deterministic so the harness mechanics are testable without a real model.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance
from seahorse.facade.types import RememberPayload


def _session(session_id: str, date: datetime, turns: list[dict]) -> dict:
    return {"session_id": session_id, "date": date, "turns": turns}


def make_synthetic_dataset() -> BenchmarkDataset:
    """A small deterministic dataset covering the 5 LongMemEval capabilities."""
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
                    [{"body": "# France\n\nThe capital of France is Paris.", "title": "France"}],
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


@pytest.fixture
def synthetic_dataset() -> BenchmarkDataset:
    return make_synthetic_dataset()


class FakeReaderLLM:
    """Deterministic reader LLM double (no real model in tests)."""

    def __init__(self, answer: str = "Paris") -> None:
        self._answer = answer
        self.calls: list[tuple[str, str]] = []

    def generate(self, question: str, context: str, question_date=None) -> str:
        self.calls.append((question, context))
        return self._answer

    def identity(self) -> dict:
        return {"model": "fake-reader", "temperature": 0.0, "seed": 42}


class FakeTokenizer:
    """Deterministic tokenizer double: 1 token per 4 chars (heuristic)."""

    def count(self, text: str) -> int:
        return max(1, len(text) // 4)


@pytest.fixture
def fake_reader() -> FakeReaderLLM:
    return FakeReaderLLM()


@pytest.fixture
def fake_tokenizer() -> FakeTokenizer:
    return FakeTokenizer()


def remember_episode(facade, body: str, *, session_id: str = "s1", title: str | None = None):
    """Helper: remember a skip-mode episode and return the WriteResult."""
    return facade.remember(
        RememberPayload(
            body=body,
            by={"source_type": "agent", "agent_id": "bench", "session_id": session_id},
            title=title,
        ),
        skip_extraction=True,
    )
