"""``ReaderLLMClient`` — the harness-controlled reader LLM (f5-16 §5.2).

The reader is the agent that USES the memory (the L3 agent), NOT Seahorse
itself. It calls LiteLLM directly (the #4 contract has no temperature/seed —
f5-16 §10.1 OQ-16-2) with t=0, seed=42, max_tokens pinned. The system prompt
is git-tracked (``harness/prompts/reader_system_prompt.txt``) and hashed into
the manifest fingerprint.
"""

from __future__ import annotations

from pathlib import Path

_PROMPT_PATH = Path(__file__).parent / "prompts" / "reader_system_prompt.txt"


class ReaderLLMClient:
    """Deterministic reader LLM client (LiteLLM direct, t=0, seed=42)."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        seed: int = 42,
        max_tokens: int = 512,
        timeout_s: float = 60.0,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def generate(self, question: str, context: str, question_date=None) -> str:
        import litellm  # noqa: F401  # type: ignore[import-not-found]  # the 'llm' extra

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": context},
        ]
        resp = litellm.completion(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            seed=self._seed,
            max_tokens=self._max_tokens,
            timeout=self._timeout_s,
        )
        return resp.choices[0].message.content or ""

    def identity(self) -> dict:
        return {
            "model": self._model,
            "temperature": self._temperature,
            "seed": self._seed,
            "max_tokens": self._max_tokens,
        }


class StubReaderLLM:
    """Retrieval-only reader double — deterministic, no litellm import.

    The F1/F3 experiment DECISION metrics (recall@10, ndcg@10, knowledge-update
    accuracy, token/latency) never consume the reader's ``answer`` (f5-16 §4.4
    honest floor — retrieval is LLM-free). A retrieval-only pass therefore
    produces the SAME decision numbers as a full QA run, without the hours of
    Ollama calls or the ``llm`` extra (f7 §5 cost note). The returned empty
    answer is never scored; ``answer``, ``reader_latency_ms`` and
    ``total_query_latency_ms`` differ from a real run and must not be compared.
    """

    def __init__(self, temperature: float = 0.0) -> None:
        self._temperature = temperature

    def generate(self, question: str, context: str, question_date=None) -> str:
        return ""

    def identity(self) -> dict:
        return {
            "model": "stub-retrieval-only",
            "temperature": self._temperature,
            "seed": None,
            "max_tokens": 0,
        }


__all__ = ["ReaderLLMClient", "StubReaderLLM"]
