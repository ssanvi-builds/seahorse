"""Tests for the judges (bias mitigations)."""

from __future__ import annotations

import pytest

from seahorse.benchmark.judges.llm_judge import LLMJudge
from seahorse.benchmark.judges.rubrics import RubricRegistry
from tests.benchmark.conftest import install_litellm


def test_rubric_registry_register_get():
    RubricRegistry.register("multi-session", "rubric text")
    assert "rubric text" in RubricRegistry.get("multi-session")


def test_rubric_registry_get_unknown_raises():
    with pytest.raises(KeyError, match="Unknown rubric"):
        RubricRegistry.get("does-not-exist")


def test_rubric_registry_hashes_deterministic():
    RubricRegistry.register("q1", "same text")
    a = RubricRegistry.hashes()["q1"]
    b = RubricRegistry.hashes()["q1"]
    assert a == b
    assert len(a) == 64


def test_rubric_registry_load_defaults():
    RubricRegistry.load_defaults()
    assert "strict" in RubricRegistry.hashes()
    assert "lenient" in RubricRegistry.hashes()


def test_llm_judge_identity():
    judge = LLMJudge("ollama/qwen2.5:7b")
    ident = judge.identity()
    assert ident["model"] == "ollama/qwen2.5:7b"
    assert ident["temperature"] == 0.0
    assert ident["seed"] == 42


def _resp_with(content: str):
    class _Msg:
        pass

    class _Choice:
        message = _Msg()

    _Choice.message.content = content
    return type("Resp", (), {"choices": [_Choice()]})()


def test_llm_judge_parses_yes(monkeypatch):
    judge = LLMJudge("ollama/qwen2.5:7b")
    install_litellm(monkeypatch, lambda **kw: _resp_with("yes"))
    assert judge.judge("Q?", "A", "G", "rubric {question} {golden} {answer}") is True


def test_llm_judge_parses_no(monkeypatch):
    judge = LLMJudge("ollama/qwen2.5:7b")
    install_litellm(monkeypatch, lambda **kw: _resp_with("no"))
    assert judge.judge("Q?", "A", "G", "rubric") is False


def test_llm_judge_pair_position_swap(monkeypatch):
    """A wins only if judged better in both orders."""
    judge = LLMJudge("ollama/qwen2.5:7b")
    calls = []

    def fake_completion(**kw):
        content = kw["messages"][0]["content"]
        calls.append(content)
        # forward: "Answer A: A" → yes; backward: "Answer A: B" → no
        return _resp_with("yes" if "Answer A: A" in content else "no")

    install_litellm(monkeypatch, fake_completion)
    # A wins only if judged better in both orders
    assert judge.judge_pair("Q?", "A", "B", "rubric") is True
    assert len(calls) == 2  # forward + backward
