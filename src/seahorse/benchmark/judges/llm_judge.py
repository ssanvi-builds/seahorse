"""``LLMJudge`` — LLM-as-judge with bias mitigations (f5-16 §6.3).

Mitigations implemented:
- **Generator ≠ judge**: the judge model is family-disjoint from the reader
  (enforced by ``BenchmarkConfig.validate()`` at startup).
- **Strict + lenient rubrics**: both are git-tracked and hashed into the
  fingerprint (f5-16 §5.3 F7).
- **Position swap**: in pairwise comparisons the order is swapped and the
  verdicts aggregated (the harness exposes ``judge_pair`` for that).
"""

from __future__ import annotations


class LLMJudge:
    """Binary LLM-as-judge over a rubric template (LiteLLM direct)."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        seed: int = 42,
        max_tokens: int = 256,
        timeout_s: float = 60.0,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s

    def judge(self, question: str, answer: str, golden_answer: str, rubric: str) -> bool:
        """Judge one answer against the golden answer with the given rubric."""
        import litellm  # noqa: F401  # type: ignore[import-not-found]  # the 'llm' extra

        prompt = rubric.format(question=question, golden=golden_answer, answer=answer)
        resp = litellm.completion(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            seed=self._seed,
            max_tokens=self._max_tokens,
            timeout=self._timeout_s,
        )
        text = (resp.choices[0].message.content or "").strip().lower()
        return "yes" in text or "correct" in text

    def judge_pair(self, question: str, answer_a: str, answer_b: str, rubric: str) -> bool:
        """Position-swapped pairwise comparison: does A beat B?

        The order is swapped and the two verdicts aggregated (f5-16 §6.3):
        A wins only if it is judged better in both orders.
        """
        a_wins = self._judge_pair_once(question, answer_a, answer_b)
        b_wins = self._judge_pair_once(question, answer_b, answer_a)
        return a_wins and not b_wins

    def _judge_pair_once(self, question: str, answer_a: str, answer_b: str) -> bool:
        import litellm  # noqa: F401  # type: ignore[import-not-found]  # the 'llm' extra

        prompt = (
            f"Question: {question}\n"
            f"Answer A: {answer_a}\n"
            f"Answer B: {answer_b}\n"
            "Is Answer A better than Answer B? Respond with exactly 'yes' or 'no'."
        )
        resp = litellm.completion(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            seed=self._seed,
            max_tokens=self._max_tokens,
            timeout=self._timeout_s,
        )
        text = (resp.choices[0].message.content or "").strip().lower()
        return "yes" in text

    def identity(self) -> dict:
        return {
            "model": self._model,
            "temperature": self._temperature,
            "seed": self._seed,
            "max_tokens": self._max_tokens,
        }


__all__ = ["LLMJudge"]
