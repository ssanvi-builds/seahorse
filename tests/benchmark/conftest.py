"""Shared fixtures for the benchmark harness tests.

The synthetic dataset is the canonical test corpus: no HuggingFace download in
CI (tests use a synthetic dataset). The fakes (reader LLM, judge)
are deterministic so the harness mechanics are testable without a real model.
The dataset builder lives in the package (``benchmark.experiments.synthetic``)
so the experiment runner reuses the SAME corpus at runtime — no duplication.
"""

from __future__ import annotations

import sys
import types

import pytest

from seahorse.benchmark.contracts import BenchmarkDataset
from seahorse.benchmark.experiments.synthetic import make_synthetic_dataset
from seahorse.facade.types import RememberPayload


def install_litellm(monkeypatch, completion_fn) -> None:
    """Install a fake ``litellm`` module so judge/reader tests run without the
    ``llm`` extra (mirror of tests/llm/test_lite_llm_backend.py)."""
    fake = types.ModuleType("litellm")
    fake.completion = completion_fn
    monkeypatch.setitem(sys.modules, "litellm", fake)


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
        return len(text) // 4


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
