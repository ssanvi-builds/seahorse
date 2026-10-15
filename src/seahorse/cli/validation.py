"""CLI-shape border helpers — caps, vocabulary, datetime, provenance, timing.

The single home for the CLI's border-validation surface. The facade owns domain
checks (empty body/query, invalid extraction_mode, PIT kind/t); the CLI owns only
what the facade does NOT enforce at the border:

- size caps (``constants.py`` budgets) via ``_require_le``;
- vocabulary membership (``source_type`` / ``cognitive_type``) via
  ``_build_provenance`` / ``_validate_cognitive_type``;
- ISO-8601 parsing via ``_parse_dt``;
- the pre-facade cap checks that must run BEFORE any heavy import (the ONNX
  stack segfaults under a large process argv — onnxruntime >= 1.29) via
  ``validate_remember_inputs`` / ``validate_improve_inputs``.

``_timed`` is the timing wrapper: ``--verbose`` wall-clock to stderr (never
stdout, which is the structured-output channel).

These are shared across the primitive commands (``primitives.py``) and the
procedural-skill commands (``skills.py``); both import from here.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from seahorse.cli.errors import CliUsageError
from seahorse.constants import (
    BODY_MAX_CHARS,
    COGNITIVE_TYPES,
    EP_ID_MAX_CHARS,
    PROVENANCE_ID_MAX_CHARS,
    REASON_MAX_CHARS,
    SOURCE_TYPES,
    SUBJECT_FILTER_MAX_CHARS,
)
from seahorse.disclosure.types import SUMMARY_MAX_CHARS
from seahorse.facade.types import Provenance

_T = TypeVar("_T")


def _timed(label: str, fn: Callable[[], _T], *, verbose: bool) -> _T:
    """Run ``fn`` and, when ``--verbose``, write its wall-clock to stderr.

    Timing is diagnostic output — it goes to stderr (never stdout, which is
    the structured output channel). ``verbose=False`` is a zero-cost passthrough
    (no perf_counter call).
    """
    if not verbose:
        return fn()
    start = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    sys.stderr.write(f"[verbose] {label} took {elapsed_ms:.1f}ms\n")
    return result


def _require_le(value: str, *, limit: int, field: str) -> None:
    """Cap enforcement at the CLI border (constants.py budgets)."""
    if len(value) > limit:
        raise CliUsageError(f"--{field} exceeds {limit} chars (got {len(value)})")


def _parse_dt(value: str, *, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CliUsageError(f"--{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    return dt


def _build_provenance(
    *,
    source_type: str,
    agent_id: str | None,
    session_id: str | None,
) -> Provenance:
    """Build the caller-authority ``Provenance`` dict (facade-owned type).

    Validates ``source_type`` membership (the facade only checks presence, not
    membership — the CLI guards the vocabulary at the border so garbage does
    not get stored verbatim by the engine). ``agent_id``/``session_id`` are
    length-capped; they are NOT required for ``source_type=agent`` in the
    current release (the facade/engine do not enforce it — documented future
    enhancement).
    """
    if source_type not in SOURCE_TYPES:
        raise CliUsageError(
            f"--source-type {source_type!r} not in {sorted(SOURCE_TYPES)}"
        )
    by: Provenance = {"source_type": source_type}
    if agent_id is not None:
        _require_le(agent_id, limit=PROVENANCE_ID_MAX_CHARS, field="agent-id")
        by["agent_id"] = agent_id
    if session_id is not None:
        _require_le(session_id, limit=PROVENANCE_ID_MAX_CHARS, field="session-id")
        by["session_id"] = session_id
    return by


def _validate_cognitive_type(value: str | None) -> None:
    if value is None:
        return
    if value not in COGNITIVE_TYPES:
        raise CliUsageError(
            f"--cognitive-type {value!r} not in {sorted(COGNITIVE_TYPES)}"
        )


def validate_remember_inputs(
    *,
    body: str,
    title: str | None = None,
    summary: str | None = None,
) -> None:
    """Cap-check the bounded remember inputs BEFORE the facade builds.

    The command bodies call this before ``ctx.obj.facade()``: the facade build
    loads the ONNX stack, which segfaults under a large process argv
    (onnxruntime >= 1.29, stack-exhaustion interaction) — so an oversized body
    must surface as the typed usage error before any heavy import runs.
    """
    _require_le(body, limit=BODY_MAX_CHARS, field="body")
    if title is not None:
        _require_le(title, limit=SUBJECT_FILTER_MAX_CHARS, field="title")
    if summary is not None:
        _require_le(summary, limit=SUMMARY_MAX_CHARS, field="summary")


def validate_improve_inputs(*, ep_id: str, new_body: str, reason: str) -> None:
    """Pre-facade cap-check for ``improve`` (same rationale as remember)."""
    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    _require_le(new_body, limit=BODY_MAX_CHARS, field="new-body")
    _require_le(reason, limit=REASON_MAX_CHARS, field="reason")


__all__ = [
    "_timed",
    "_require_le",
    "_parse_dt",
    "_build_provenance",
    "_validate_cognitive_type",
    "validate_remember_inputs",
    "validate_improve_inputs",
]
