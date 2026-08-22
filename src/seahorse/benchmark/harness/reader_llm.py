"""``ReaderLLMClient`` — the harness-controlled reader LLM.

The reader is the agent that USES the memory, NOT Seahorse itself. It calls
LiteLLM directly (the LLM backend contract has no temperature/seed) with t=0,
seed=42, max_tokens pinned. The system prompt is git-tracked
(``harness/prompts/reader_system_prompt.txt``) and hashed into the manifest
fingerprint.
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
        import litellm  # type: ignore[import-not-found]  # noqa: F401  # the 'llm' extra

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\nRetrieved context:\n{context}"
                ),
            },
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

    The DECISION metrics (recall@10, ndcg@10, knowledge-update accuracy,
    token/latency) never consume the reader's ``answer`` (honest floor —
    retrieval is LLM-free). A retrieval-only pass therefore produces the SAME
    decision numbers as a full QA run, without the hours of Ollama calls or the
    ``llm`` extra. The returned empty answer is never scored; ``answer``,
    ``reader_latency_ms`` and ``total_query_latency_ms`` differ from a real run
    and must not be compared.
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
