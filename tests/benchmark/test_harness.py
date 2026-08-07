"""Tests for the harness (reader LLM + tokenizer, f5-16 §5.2/§4.4 F4)."""

from __future__ import annotations

from seahorse.benchmark.harness.reader_llm import ReaderLLMClient, StubReaderLLM
from seahorse.benchmark.harness.tokenizer import Tokenizer


def test_stub_reader_is_deterministic_no_litellm():
    """The retrieval-only reader never imports litellm (works without the llm
    extra) and is deterministic — the experiment decision metrics (recall@10 /
    ndcg@10) never consume the reader's answer (f5-16 §4.4 honest floor)."""
    reader = StubReaderLLM()
    assert reader.generate("Q?", "context") == ""
    assert reader.generate("Q2?", "ctx", question_date=None) == ""
    assert "litellm" not in StubReaderLLM.__module__


def test_stub_reader_identity():
    reader = StubReaderLLM()
    ident = reader.identity()
    assert ident["model"] == "stub-retrieval-only"
    assert ident["temperature"] == 0.0


def test_reader_llm_identity():
    client = ReaderLLMClient("ollama/qwen3:1.7b")
    ident = client.identity()
    assert ident["model"] == "ollama/qwen3:1.7b"
    assert ident["temperature"] == 0.0
    assert ident["seed"] == 42


def test_reader_llm_system_prompt_loaded():
    client = ReaderLLMClient("ollama/qwen3:1.7b")
    assert "context" in client._system_prompt  # the git-tracked prompt file


def test_reader_llm_generate(monkeypatch):
    client = ReaderLLMClient("ollama/qwen3:1.7b")

    class _Msg:
        content = "Paris"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    monkeypatch.setattr("litellm.completion", lambda **kw: _Resp())
    assert client.generate("Q?", "context") == "Paris"


def test_tokenizer_heuristic_fallback():
    """The deterministic chars/4 heuristic is used when tiktoken is absent."""
    tok = Tokenizer()
    tok._enc = False  # force the fallback path
    assert tok.count("x" * 40) == 10
    assert tok.count("") == 1  # max(1, ...)


def test_tokenizer_real_count_when_tiktoken_available():
    """With tiktoken installed, the count is the REAL token count."""
    tok = Tokenizer()
    assert tok.count("hello world") > 0


def test_tokenizer_encoding_name():
    tok = Tokenizer(encoding_name="cl100k_base")
    assert tok._encoding_name == "cl100k_base"
