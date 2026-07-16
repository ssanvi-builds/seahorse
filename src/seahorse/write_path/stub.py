"""#5 ``StubWritePath`` — the MVP-0 write path (SO-5b).

MVP-0 materializes the **skip path for real**: ``run_skip_path`` is the
production skip route, not dead code. The ``llm`` path degrades to skip with
``reason='llm_not_implemented_mvp0'`` because the LLM extraction client is
MVP-1 (``StubLLMClient`` raises ``NotImplementedError``).

What #5 owns in MVP-0:
- Building the **effective provenance** for the skip path: it corrects
  ``extraction_mode='skip'``, ``model_used=None``, ``prompt_hash=None`` so the
  stored ``Episode.provenance`` never lies about the effective mode (ADR-09 /
  ADR-10 honesty). The caller's other provenance keys pass through verbatim.

What #5 does NOT do in MVP-0 (MVP-1):
- The ``is_valid_skip_path`` formal gate (engine #2 already exposes it; #5
  wires it in MVP-1).
- ``deterministic_extract`` fallback when the note has no populated frontmatter.
- The real ``llm`` path via ``#4.extract`` + ``BudgetContext``.

#5 does NOT set ``created_at`` / ``invalid_at`` / ``expired_at`` / ``id`` /
``supersedes`` (engine-owned, I1) and does NOT write ``AuditEvent`` (engine).
It delegates to ``engine.remember`` and returns its ``WriteResult`` verbatim.

References:
- f6-signoffs.md SO-5b (StubWritePath, run_skip_path, _degrade_to_skip)
- f5-05-skip-extraction.md §3 (skip path), §5 (degraded state)
- seahorse/engine/engine.py (BiTemporalEngine.remember — the single write entry)
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import ExtractionMode, RememberPayload
from seahorse.write_path.decide import PathDecision, decide_path


@runtime_checkable
class _EngineLike(Protocol):
    # Structural seam: anything with a ``remember`` matching the engine signature.
    def remember(
        self,
        *,
        body: str,
        by: dict,
        valid_at: datetime | None = ...,
        cognitive_type: str | None = ...,
        schema_version: str = ...,
        title: str | None = ...,
        now: datetime | None = ...,
    ) -> WriteResult: ...


def run_skip_path(
    payload: RememberPayload,
    decision: PathDecision,
    engine: _EngineLike,
    *,
    now: datetime | None = None,
) -> WriteResult:
    """Execute the deterministic skip path: build effective provenance, delegate.

    Sets ``extraction_mode='skip'``, ``model_used=None``, ``prompt_hash=None``
    on the effective provenance (the caller's other keys pass through). Does
    NOT touch ``confidence`` (caller authority). Delegates to ``engine.remember``
    and returns its ``WriteResult`` verbatim.
    """
    by = {
        **payload.by,
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
    }
    return engine.remember(
        body=payload.body,
        by=by,
        valid_at=payload.valid_at,
        cognitive_type=payload.cognitive_type,
        schema_version=payload.schema_version,
        title=payload.title,
        now=now,
    )


def _degrade_to_skip(
    payload: RememberPayload,
    decision: PathDecision,
    engine: _EngineLike,
    *,
    now: datetime | None = None,
    reason: str = "llm_not_implemented_mvp0",
) -> WriteResult:
    """Degrade an ``llm`` decision to the skip path (MVP-0 honesty).

    MVP-0 has no LLM client, so the ``llm`` path degrades directly to skip
    (it never reaches ``StubLLMClient``). The effective mode is corrected to
    ``skip`` — #5 does not lie about the effective mode. The ``reason`` records
    why the degradation happened (observability).
    """
    skip_decision = PathDecision(
        path="skip", requested_mode=decision.requested_mode, reason=reason
    )
    return run_skip_path(payload, skip_decision, engine, now=now)


class StubWritePath:
    """MVP-0 ``WritePath`` — decides the path, then runs skip or degrades llm.

    Single write-path from day one (ADR-09): #12/#13/#14 call ``ingest`` rather
    than inlining the skip. ``decide_path`` is pure (no LLM); ``run_skip_path``
    is the real production skip route.
    """

    def __init__(self, engine: _EngineLike, repo: object | None = None) -> None:
        # ``repo`` is accepted for forward-compat (MVP-1 _degrade_to_skip uses
        # #3 derive_subject for deterministic_extract). Unused in MVP-0.
        self._engine = engine
        self._repo = repo

    def ingest(
        self,
        payload: RememberPayload,
        extraction_mode: ExtractionMode,
        *,
        now: datetime | None = None,
    ) -> WriteResult:
        decision = decide_path(payload, extraction_mode)
        if decision.path == "llm":
            return _degrade_to_skip(payload, decision, self._engine, now=now)
        return run_skip_path(payload, decision, self._engine, now=now)


__all__ = ["StubWritePath", "run_skip_path", "_degrade_to_skip"]