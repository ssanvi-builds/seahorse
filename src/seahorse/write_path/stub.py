"""#5 ``StubWritePath`` — the MVP-0 write path (SO-5b).

MVP-0 materializes the **skip path for real** and first-class: ``run_skip_path``
runs the engine ``is_valid_skip_path`` formal gate, falls back to
``deterministic_extract`` (zero-LLM editorial pass) when the gate rejects, and
owns ``confidence`` in the effective provenance (``1.0`` gate-valid,
``None`` fallback). The ``llm`` path still degrades to skip with
``reason='llm_not_implemented_mvp0'`` because the LLM extraction client is
MVP-1 (``StubLLMClient`` raises ``NotImplementedError``); the seam is stable
for ``#4`` to plug a real LLM path later.

What #5 owns:
- The **effective provenance** for the skip path: it sets ``extraction_mode='skip'``,
  ``model_used=None``, ``prompt_hash=None``, and ``confidence`` (1.0 / None) so the
  stored ``Episode.provenance`` never lies about the effective mode (ADR-09/ADR-10).
  ``confidence`` is #5-owned (f5-05 sec 11.5): #5 OVERWRITES the caller's value.
- The **gate + fallback**: a transient candidate ``Episode`` is built with
  ``created_at=now`` injected ONLY for gate validation (I1 preserved —
  ``engine.remember``/``apply_fact`` re-fix ``created_at`` on the real write).

What #5 does NOT do (MVP-1 / engine-owned):
- The real ``llm`` path via ``#4.extract`` + ``BudgetContext``.
- Set ``created_at`` / ``invalid_at`` / ``expired_at`` / ``id`` (engine-owned, I1/I4/I5).
  ``supersedes`` is #5-owned (``None`` in ``remember``; engine sets it only on
  improve/forget) but absent from the editorial candidate and injected as ``None``.
- Write ``AuditEvent`` (engine, ADR-03 single-writer).

References:
- f6-signoffs.md SO-5b (StubWritePath, run_skip_path, _degrade_to_skip)
- f5-05-skip-extraction.md §3 (skip path), §3.1 (gate), §3.2 (fallback), §11.5 (field ownership)
- seahorse/engine/engine.py (is_valid_skip_path — the gate; remember — the single write)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from seahorse.contracts.engine import WriteResult
from seahorse.contracts.episode import Episode
from seahorse.engine.errors import E_SKIP_CONTRACT_VIOLATED, EngineError
from seahorse.facade.types import ExtractionMode, RememberPayload
from seahorse.write_path.decide import PathDecision, decide_path
from seahorse.write_path.extract import deterministic_extract

_logger = logging.getLogger("seahorse.write_path.stub")


@runtime_checkable
class _EngineLike(Protocol):
    # Structural seam: anything with ``remember`` + ``is_valid_skip_path``.
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

    def is_valid_skip_path(self, ep: Episode) -> bool: ...


def _resolve_now(now: datetime | None) -> datetime:
    """Aware UTC ``now`` for the transient gate candidate (never naive)."""
    return now if now is not None else datetime.now(UTC)


def _build_candidate(payload: RememberPayload, now: datetime) -> Episode:
    """Transient candidate for gate validation ONLY (f5-05 sec 3.1).

    ``created_at`` is injected here just so the gate (which requires it
    non-None) can run; it is NEVER persisted — ``engine.remember``/``apply_fact``
    re-fix ``created_at`` on the real write (I1). ``invalid_at`` / ``expired_at``
    / ``supersedes`` are ``None`` (engine-owned / #5-owned-at-remember). The
    provenance carries the effective skip-mode keys the gate reads.
    """
    source = payload.by.get("source_type")
    return Episode(
        id="",  # engine-owned; the gate does not read it
        created_at=now,
        schema_version=payload.schema_version,
        provenance={
            **payload.by,
            "extraction_mode": "skip",
            "model_used": None,
            "prompt_hash": None,
        },
        body=payload.body,
        title=payload.title,
        valid_at=payload.valid_at,
        cognitive_type=payload.cognitive_type,
        invalid_at=None,
        expired_at=None,
        supersedes=None,
        source_type=source if isinstance(source, str) else None,
        tags=list(payload.tags),
    )


def _effective_by(payload: RememberPayload, confidence: float | None) -> dict:
    """Effective provenance for the skip path (f5-05 sec 11.5).

    Four keys added over the caller's ``by``: ``extraction_mode='skip'``,
    ``model_used=None``, ``prompt_hash=None``, ``confidence`` (1.0 gate-valid,
    None fallback). ``confidence`` OVERWRITES any caller value (#5-owned).
    """
    return {
        **payload.by,
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
        "confidence": confidence,
    }


def _fallback_remember(
    payload: RememberPayload,
    engine: _EngineLike,
    *,
    now: datetime | None,
) -> WriteResult:
    """Gate rejected -> zero-LLM editorial fallback (f5-05 sec 3.2).

    ``deterministic_extract`` raises ``SubjectDerivationError`` loud when no
    subject is derivable (ADR-10); otherwise the candidate is sound and we
    delegate to ``engine.remember`` with ``confidence=None``.
    """
    # Loud subject check — raises SubjectDerivationError if no title/H1.
    deterministic_extract(payload)
    return engine.remember(
        body=payload.body,
        by=_effective_by(payload, confidence=None),
        valid_at=payload.valid_at,
        cognitive_type=payload.cognitive_type,
        schema_version=payload.schema_version,
        title=payload.title,
        now=now,
    )


def run_skip_path(
    payload: RememberPayload,
    decision: PathDecision,
    engine: _EngineLike,
    *,
    now: datetime | None = None,
) -> WriteResult:
    """Execute the first-class deterministic skip path: gate, fallback, delegate.

    1. Build a transient candidate ``Episode`` (``created_at=now`` injected for
       gate validation only — I1 preserved).
    2. Run ``engine.is_valid_skip_path``:
       - raises ``EngineError(E_SKIP_CONTRACT_VIOLATED)`` -> log
         ``skip_path.populated_invalid`` and fall back to ``deterministic_extract``
         (``confidence=None``); ``SubjectDerivationError`` propagates loud if no
         subject is derivable.
       - returns ``False`` -> same fallback (symmetric; e.g. extraction_mode mismatch).
       - returns ``True`` -> use-as-is with ``confidence=1.0``.
    3. Delegate to ``engine.remember`` and return its ``WriteResult`` verbatim.

    ``decision`` is accepted for signature stability (the path is already chosen);
    it is not re-derived here.
    """
    candidate = _build_candidate(payload, _resolve_now(now))
    try:
        valid = engine.is_valid_skip_path(candidate)
    except EngineError as exc:
        if exc.code != E_SKIP_CONTRACT_VIOLATED:
            raise
        _logger.warning(
            "skip_path.populated_invalid: gate rejected candidate "
            "(field=%s); falling back to deterministic_extract",
            exc.context.get("field"),
        )
        return _fallback_remember(payload, engine, now=now)
    if not valid:
        return _fallback_remember(payload, engine, now=now)
    return engine.remember(
        body=payload.body,
        by=_effective_by(payload, confidence=1.0),
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
    is the real production skip route (gate + fallback + confidence).
    """

    def __init__(self, engine: _EngineLike, repo: object | None = None) -> None:
        # ``repo`` is accepted for forward-compat (MVP-1 deterministic_extract
        # may use #3 derive_subject with a path). Unused in MVP-0 skip-path-only.
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