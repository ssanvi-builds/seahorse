"""``RubricRegistry`` — git-tracked judge rubrics, hashed into the fingerprint.

The rubrics live in ``harness/prompts/judge/`` (git-tracked):
"lenient" = the canonical LongMemEval rubric, "strict" = atomic-claims variant.
A rubric bump changes its SHA-256 and invalidates the cache automatically.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_JUDGE_DIR = Path(__file__).parent.parent / "harness" / "prompts" / "judge"


class RubricRegistry:
    """Registry of rubric templates, keyed by question_type."""

    _rubrics: dict[str, str] = {}

    @classmethod
    def register(cls, question_type: str, rubric_text: str) -> None:
        cls._rubrics[question_type] = rubric_text

    @classmethod
    def get(cls, question_type: str) -> str:
        if question_type not in cls._rubrics:
            raise KeyError(f"Unknown rubric for question_type: {question_type}")
        return cls._rubrics[question_type]

    @classmethod
    def hashes(cls) -> dict[str, str]:
        return {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in cls._rubrics.items()}

    @classmethod
    def load_defaults(cls) -> None:
        """Load the git-tracked strict + lenient rubrics (idempotent)."""
        for name in ("strict", "lenient"):
            path = _JUDGE_DIR / f"{name}.txt"
            if path.exists():
                cls._rubrics.setdefault(name, path.read_text(encoding="utf-8"))


__all__ = ["RubricRegistry"]
