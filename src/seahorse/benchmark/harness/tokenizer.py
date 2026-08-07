"""``Tokenizer`` — REAL token counting via the reader LLM tokenizer (f5-16 §4.4 F4).

Uses ``tiktoken`` (the ``benchmark`` extra) with a configurable encoding;
falls back to a deterministic heuristic (chars/4) when tiktoken is absent so
the harness stays importable without the extra. The measured count is what the
efficiency metrics use — never ``len*50``.
"""

from __future__ import annotations

from typing import Any


class Tokenizer:
    """Counts tokens with tiktoken (or a deterministic heuristic fallback)."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._encoding_name = encoding_name
        self._enc: Any = None  # lazy: None = not tried, False = unavailable

    def count(self, text: str) -> int:
        if self._enc is None:
            try:
                import tiktoken  # noqa: F401  # type: ignore[import-not-found]  # the 'benchmark' extra

                self._enc = tiktoken.get_encoding(self._encoding_name)
            except ImportError:
                self._enc = False
        if self._enc:
            return len(self._enc.encode(text))
        return max(1, len(text) // 4)  # deterministic heuristic fallback


__all__ = ["Tokenizer"]
