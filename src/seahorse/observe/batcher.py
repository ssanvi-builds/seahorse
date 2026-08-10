"""Deterministic turn render for the observer (obsiforge §4.3).

The batcher is a PURE function of the turn: the body does NOT include ``ts``,
``session_id``, ``prompt_number`` or ``cwd`` — a reprocess after crash must
produce the same hash (ADR-10). Order = arrival sequence. Truncation is
deterministic by byte (never splits a UTF-8 codepoint). Redaction happens
BEFORE the render (at enqueue), so the render itself is pure.

The H1 carries collision uniqueness: ``title = "{first line of prompt truncated}
[{session_tag}:{prompt_number}]"`` — stable across reprocess (prompt_number is
persisted in the observer DB, obsiforge §15.2), distinct between turns of the
same session, distinct between sessions → I11 only collides for the SAME turn
re-emitted, never for legitimate turns.

References:
- obsiforge-evolution-architecture.md §4.3 (deterministic batcher)
- obsiforge-evolution-architecture.md §15.2 redesign 1 (clustering key)
- seahorse/engine/canonical.py (canonical_body_hash, reused for fingerprints)
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from seahorse.engine.canonical import canonical_body_hash as _canonical_body_hash

# The H1 becomes the subject (title > H1 > None, SO-2); SUBJECT_MAX_CHARS=160
# (f5-09 §2.1) bounds the title so the derived subject is never truncated away.
TITLE_MAX_CHARS = 160
# A turn body cap: generous but bounded (deterministic byte truncation).
BODY_MAX_CHARS = 8000

_FALLBACK_FIRST_LINE = "untitled"


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """Truncate ``text`` to ``max_bytes`` UTF-8 bytes without splitting a codepoint.

    Deterministic (ADR-10): the same input always truncates the same way.
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Walk back from the boundary until we land on a codepoint start.
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def build_title(
    prompt: str,
    session_tag: str,
    prompt_number: int,
    *,
    max_chars: int = TITLE_MAX_CHARS,
) -> str:
    """H1 with collision uniqueness: ``"{first line} [{session_tag}:{n}]"``.

    The first line of the prompt is truncated so the full title (first line +
    tag) fits ``max_chars``. The tag always survives — it is the collision
    discriminator. An empty first line falls back to ``untitled``.
    """
    first_line = prompt.split("\n", 1)[0].strip() or _FALLBACK_FIRST_LINE
    tag = f"[{session_tag}:{prompt_number}]"
    budget = max_chars - len(tag) - 1  # -1 for the separating space
    if budget < 1:
        budget = 1
    truncated = _truncate_bytes(first_line, budget)
    return f"{truncated} {tag}"


def render_turn_body(
    prompt: str,
    events: Sequence[Mapping[str, Any]],
    *,
    session_tag: str,
    prompt_number: int,
    max_chars: int = BODY_MAX_CHARS,
) -> str:
    """Deterministic render of a turn into an episode body. Pure.

    ``events`` are the already-redacted tool events in arrival order. The body
    starts with the H1 title, then the user prompt, then each tool event. No
    ``ts`` / ``session_id`` / ``prompt_number`` / ``cwd`` fields (reprocess
    stability). Byte-truncated deterministically.
    """
    title = build_title(prompt, session_tag, prompt_number)
    lines: list[str] = [f"# {title}", "", "## User prompt", "", prompt]
    for event in events:
        tool_name = event.get("tool_name", "unknown")
        tool_use_id = event.get("tool_use_id", "")
        tool_input = event.get("tool_input", "")
        tool_response = event.get("tool_response", "")
        lines.append("")
        lines.append(f"### {tool_name}")
        if tool_use_id:
            lines.append(f"tool_use_id: {tool_use_id}")
        lines.append(f"input: {tool_input}")
        lines.append(f"response: {tool_response}")
    body = "\n".join(lines)
    return _truncate_bytes(body, max_chars)


def canonical_body_hash(body: str) -> str:
    """SHA-256 hex of the canonicalized body (reuses the engine's canonicalizer).

    The canonicalizer normalizes NFC + trailing whitespace + blank-line runs, so
    a reprocess that differs only in cosmetic whitespace still hashes the same.
    """
    return _canonical_body_hash(body)


def event_fingerprint(payload: Mapping[str, Any]) -> str:
    """Canonical hash of a redacted event payload (queue-level dedup key).

    ``canonical_body_hash(json.dumps(payload, sort_keys, compact))`` — the
    same redacted payload always hashes the same, so a re-emitted event is a
    no-op (INSERT OR IGNORE, obsiforge §4.5 layer 1).
    """
    canonical = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _canonical_body_hash(canonical)


__all__ = [
    "TITLE_MAX_CHARS",
    "BODY_MAX_CHARS",
    "build_title",
    "render_turn_body",
    "canonical_body_hash",
    "event_fingerprint",
]
