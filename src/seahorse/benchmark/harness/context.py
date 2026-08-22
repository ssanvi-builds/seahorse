"""Reader-context assembler — the configurable representation the reader sees.

Progressive disclosure (INDEX/TIMELINE carry NO body) makes the reader's context
summary-only by default: ``[subject] summary`` (~200 chars, the first sentence).
The reader-context finding (A4, 2026-08-21) showed that representation cannot
support extractive answers — end-to-end accuracy 0.070 vs recall@10 0.790 on
the LMEB-S subsample, corroborated by ``rerank_body`` (body 0.830 vs summary
0.660) — because LMEB golden answers sit MID-TURN in the body.

This module is the seam that makes the representation configurable:
- ``summary`` — the baseline (``[subject] summary``).
- ``body`` — the FULL hydrated bodies (the upper bound; the same signal that
  re-opened the rerank decision).
- ``body_bounded`` — bodies capped per episode (``BODY_MAX_CHARS``) — the
  realistic product trade-off.

The assembler is PURE (no facade): ``body_for`` is an injected callable mapping
ep_id → body (None → the summary fallback line). The facade coupling lives in
``batch_body_for`` (the wiring layer), which batches ``recall_full`` within the
``MAX_FULL_BATCH`` cap.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

from seahorse.disclosure.types import MAX_FULL_BATCH

# The context representation modes (a CLI/experiment-configurable axis).
ContextMode = Literal["summary", "body", "body_bounded"]

# The per-episode body cap for ``body_bounded`` (~10x the 200-char summary —
# bounded enough to keep the reader window efficient, long enough to carry the
# mid-turn answer the summary loses).
BODY_MAX_CHARS: int = 2000

# ``body_for`` maps ep_id → body; None when the body is unavailable.
BodyFor = Callable[[str], str | None]


def assemble_context(
    rows,
    *,
    mode: ContextMode = "summary",
    body_for: BodyFor | None = None,
) -> str:
    """Render the reader context for the top-k rows in the given mode.

    ``rows`` are IndexRow-like (``ep_id`` / ``subject`` / ``summary``); the pure
    assembler never imports the facade. In the body modes, a missing body (no
    ``body_for`` or unknown ep_id) falls back to the summary line — the context
    never drops a retrieved row.
    """
    if mode not in ("summary", "body", "body_bounded"):
        raise ValueError(f"unknown context mode: {mode!r} (expected summary|body|body_bounded)")
    lines: list[str] = []
    for i, r in enumerate(rows, 1):
        subject = r.subject or ""
        snippet = r.summary or r.subject or ""
        if mode == "summary":
            lines.append(f"{i}. [{subject}] {snippet}")
            continue
        body = body_for(r.ep_id) if body_for is not None else None
        if body is None:
            lines.append(f"{i}. [{subject}] {snippet}")
            continue
        if mode == "body_bounded":
            body = body[:BODY_MAX_CHARS]
        lines.append(f"{i}. [{subject}]\n{body}")
    return "\n".join(lines)


def batch_body_for(
    facade,
    ep_ids: Sequence[str],
    *,
    batch_size: int = MAX_FULL_BATCH,
) -> dict[str, str]:
    """Hydrate bodies via ``recall_full`` in batches (the wiring layer).

    ``recall_full`` raises ``FullBatchTooLarge`` above ``MAX_FULL_BATCH`` per
    call, so the top-k is fetched in deduped batches. Returns ``{ep_id: body}``
    for the stored episodes; absent ep_ids simply miss (the assembler falls back
    to summary). PIT in FULL is not supported in the current release, so
    hydration is always active-now (``pit=None``).
    """
    bodies: dict[str, str] = {}
    ep_list = list(dict.fromkeys(ep_ids))  # dedup, preserve order
    for i in range(0, len(ep_list), batch_size):
        batch = ep_list[i : i + batch_size]
        for detail in facade.recall_full(batch):
            body = detail.episode.body or ""
            if body:
                bodies[detail.episode.id] = body
    return bodies


__all__ = [
    "BODY_MAX_CHARS",
    "ContextMode",
    "assemble_context",
    "batch_body_for",
]
