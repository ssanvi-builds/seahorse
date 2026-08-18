"""``StubWritePath`` — the first-release write path.

The first release materializes the **skip path for real** and first-class:
``run_skip_path`` runs the engine ``is_valid_skip_path`` formal gate, falls back
to ``deterministic_extract`` (zero-LLM editorial pass) when the gate rejects,
and owns ``confidence`` in the effective provenance (``1.0`` gate-valid,
``None`` fallback). The ``llm`` path still degrades to skip with
``reason='llm_not_implemented_mvp0'`` because the LLM extraction client is not
yet implemented (``StubLLMClient`` raises ``NotImplementedError``); the
extension point is stable for the LLM backend to plug a real LLM path later.

The llm→skip degrade is HONEST: provenance core carries the corrected effective
mode (``extraction_mode='skip'``, ``model_used=None``, ``prompt_hash=None`` —
no model ran on the episode); the caller's CLAIMED ``model_used`` /
``prompt_hash`` (the LLM intent that was degraded) are LOGGED for traceability,
NOT stored in core; and the degrade is marked EXPLICITLY in core
(``degraded_from`` + ``degrade_reason``) so a degraded episode is
distinguishable from a genuine skip (no "permanent lie" faking
skip-from-the-start). Genuine skip carries no degrade marker.

What the write path owns:
- The **effective provenance** for the skip path: it sets ``extraction_mode='skip'``,
  ``model_used=None``, ``prompt_hash=None``, and ``confidence`` (1.0 / None) so the
  stored ``Episode.provenance`` never lies about the effective mode.
  ``confidence`` is write-path-owned: it OVERWRITES the caller's value.
  On an llm→skip degrade it ALSO stamps the durable degrade marker
  (``degraded_from`` / ``degrade_reason``); the caller's claimed LLM intent is
  logged, not stored.
- The **gate + fallback**: a transient candidate ``Episode`` is built with
  ``created_at=now`` injected ONLY for gate validation
  (``engine.remember``/``apply_fact`` re-fix ``created_at`` on the real write).

What the write path does NOT do (engine-owned):
- The real ``llm`` path via the LLM client's ``extract`` + ``BudgetContext``.
- Set ``created_at`` / ``invalid_at`` / ``expired_at`` / ``id`` (engine-owned).
  ``supersedes`` is write-path-owned (``None`` in ``remember``; engine sets it
  only on improve/forget) but absent from the editorial candidate and injected
  as ``None``.
- Write ``AuditEvent`` (the engine is the single writer).

References:
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
from seahorse.llm import LLMClient
from seahorse.write_path.decide import PathDecision, decide_path
from seahorse.write_path.extract import derive_summary, deterministic_extract

_logger = logging.getLogger("seahorse.write_path.stub")


@runtime_checkable
class _EngineLike(Protocol):
    # Structural extension point: anything with ``remember`` + ``is_valid_skip_path``.
    def remember(
        self,
        *,
        body: str,
        by: dict,
        valid_at: datetime | None = ...,
        cognitive_type: str | None = ...,
        schema_version: str = ...,
        title: str | None = ...,
        summary: str | None = ...,
        subject: str | None = ...,
        now: datetime | None = ...,
    ) -> WriteResult: ...

    def is_valid_skip_path(self, ep: Episode) -> bool: ...


@runtime_checkable
class _IndexerLike(Protocol):
    """Best-effort retrieval indexer hook (RetrievalIndexer)."""

    def index_episode(self, ep_id: str) -> None: ...


def _resolve_now(now: datetime | None) -> datetime:
    """Aware UTC ``now`` for the transient gate candidate (never naive)."""
    return now if now is not None else datetime.now(UTC)


def _effective_summary(payload: RememberPayload) -> str | None:
    """The caller's ``summary`` or a deterministic zero-LLM fallback (first
    sentence of the body, ``SUMMARY_MAX_CHARS=200``).

    Covers 100% of episodes including the skip path — the write path always
    supplies a summary to ``engine.remember``, so the INDEX row never degrades
    to an empty snippet (deterministic, no LLM)."""
    return payload.summary or derive_summary(payload.body)


def _build_candidate(payload: RememberPayload, now: datetime) -> Episode:
    """Transient candidate for gate validation ONLY.

    ``created_at`` is injected here just so the gate (which requires it
    non-None) can run; it is NEVER persisted — ``engine.remember``/``apply_fact``
    re-fix ``created_at`` on the real write. ``invalid_at`` / ``expired_at`` /
    ``supersedes`` are ``None`` (engine-owned / write-path-owned-at-remember).
    The provenance carries the effective skip-mode keys the gate reads.
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


def _effective_by(
    payload: RememberPayload,
    confidence: float | None,
    *,
    degrade: tuple[str, str] | None = None,
) -> dict:
    """Effective provenance for the skip path.

    Four keys added over the caller's ``by``: ``extraction_mode='skip'``,
    ``model_used=None``, ``prompt_hash=None``, ``confidence`` (1.0 gate-valid,
    None fallback). ``confidence`` OVERWRITES any caller value (write-path-owned).

    ``degrade`` (set ONLY on an llm→skip degrade) adds the durable degrade
    marker — ``degraded_from`` (the requested mode that was degraded) and
    ``degrade_reason`` (why) — so a degraded episode is distinguishable from a
    genuine skip in stored provenance (no silent degradation, no "permanent
    lie" that fakes skip-from-the-start). Genuine skip passes ``degrade=None``
    and carries no marker. The caller's CLAIMED ``model_used``/``prompt_hash``
    never reach core (overwritten to ``None`` here); they are logged by
    ``_log_llm_intent`` instead.
    """
    by = {
        **payload.by,
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
        "confidence": confidence,
    }
    if degrade is not None:
        degraded_from, degrade_reason = degrade
        by["degraded_from"] = degraded_from
        by["degrade_reason"] = degrade_reason
    return by


def _log_llm_intent(payload: RememberPayload, reason: str) -> None:
    """Log the caller's CLAIMED LLM intent for traceability.

    On an llm→skip degrade the EFFECTIVE mode is skip and provenance core carries
    ``model_used=None`` / ``prompt_hash=None`` (no model ran on this episode —
    ``_effective_by`` overwrites them). The caller's claimed ``model_used`` /
    ``prompt_hash`` — the LLM intent that was degraded — are traced here at INFO,
    NOT stored in provenance core. Logs are the traceability channel for the
    failed intent; the durable degrade marker (``degraded_from`` /
    ``degrade_reason``) is what lives in core.
    """
    _logger.info(
        "write_path.llm_degraded_to_skip reason=%s intent_model_used=%s "
        "intent_prompt_hash=%s",
        reason,
        payload.by.get("model_used"),
        payload.by.get("prompt_hash"),
    )


def _fallback_remember(
    payload: RememberPayload,
    engine: _EngineLike,
    *,
    now: datetime | None,
    degrade: tuple[str, str] | None = None,
) -> WriteResult:
    """Gate rejected -> zero-LLM editorial fallback.

    ``deterministic_extract`` raises ``SubjectDerivationError`` loudly when no
    subject is derivable; otherwise the candidate is sound and we delegate to
    ``engine.remember`` with ``confidence=None``. ``degrade`` threads the durable
    degrade marker through so a degraded llm→skip that falls back to
    ``deterministic_extract`` still carries the marker (not faked as skip).
    """
    # Loud subject check — raises SubjectDerivationError if no title/H1.
    deterministic_extract(payload)
    return engine.remember(
        body=payload.body,
        by=_effective_by(payload, confidence=None, degrade=degrade),
        valid_at=payload.valid_at,
        cognitive_type=payload.cognitive_type,
        schema_version=payload.schema_version,
        title=payload.title,
        summary=_effective_summary(payload),
        now=now,
    )


def run_skip_path(
    payload: RememberPayload,
    decision: PathDecision,
    engine: _EngineLike,
    *,
    now: datetime | None = None,
    degrade: tuple[str, str] | None = None,
) -> WriteResult:
    """Execute the first-class deterministic skip path: gate, fallback, delegate.

    1. Build a transient candidate ``Episode`` (``created_at=now`` injected for
       gate validation only).
    2. Run ``engine.is_valid_skip_path``:
       - raises ``EngineError(E_SKIP_CONTRACT_VIOLATED)`` -> log
         ``skip_path.populated_invalid`` and fall back to ``deterministic_extract``
         (``confidence=None``); ``SubjectDerivationError`` propagates loud if no
         subject is derivable.
       - returns ``False`` -> same fallback (symmetric; e.g. extraction_mode mismatch).
       - returns ``True`` -> use-as-is with ``confidence=1.0``.
    3. Delegate to ``engine.remember`` and return its ``WriteResult`` verbatim.

    ``decision`` is accepted for signature stability (the path is already chosen);
    it is not re-derived here. ``degrade`` (set only by ``_degrade_to_skip``)
    threads the durable degrade marker into the effective provenance on BOTH the
    gate-valid and fallback branches, so a degraded llm→skip is never faked as a
    genuine skip. Genuine-skip callers leave ``degrade=None``.
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
        return _fallback_remember(payload, engine, now=now, degrade=degrade)
    if not valid:
        return _fallback_remember(payload, engine, now=now, degrade=degrade)
    return engine.remember(
        body=payload.body,
        by=_effective_by(payload, confidence=1.0, degrade=degrade),
        valid_at=payload.valid_at,
        cognitive_type=payload.cognitive_type,
        schema_version=payload.schema_version,
        title=payload.title,
        summary=_effective_summary(payload),
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
    """Degrade an ``llm`` decision to the skip path (first-release honesty).

    The first release has no LLM client, so the ``llm`` path degrades directly
    to skip (it never reaches ``StubLLMClient``). The effective mode is corrected
    to ``skip`` — the write path does not lie about the effective mode:
    provenance core carries ``model_used=None`` / ``prompt_hash=None``. The
    caller's CLAIMED ``model_used`` / ``prompt_hash`` (the LLM intent that was
    degraded) are LOGGED for traceability via ``_log_llm_intent`` — they do NOT
    go to provenance core. The degrade is marked EXPLICITLY in core
    (``degraded_from`` = the requested mode, ``degrade_reason`` = ``reason``) so
    the stored episode is distinguishable from a genuine skip (no silent
    degradation, no "permanent lie" faking skip-from-the-start).
    """
    _log_llm_intent(payload, reason)
    skip_decision = PathDecision(
        path="skip", requested_mode=decision.requested_mode, reason=reason
    )
    degraded_from = decision.requested_mode or "llm"
    return run_skip_path(payload, skip_decision, engine, now=now, degrade=(degraded_from, reason))


class StubWritePath:
    """First-release ``WritePath`` — decides the path, then runs skip or degrades llm.

    Single write-path from day one: the facade, the MCP server, and the CLI call
    ``ingest`` rather than inlining the skip. ``decide_path`` is pure (no LLM);
    ``run_skip_path`` is the real production skip route (gate + fallback +
    confidence).
    """

    def __init__(
        self,
        engine: _EngineLike,
        indexer: _IndexerLike | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        # ``indexer``: when wired, an ACTIVE write triggers the best-effort
        # retrieval index (vec0 + FTS) — never fails the write.
        # ``llm_client``: when wired, ``ingest`` routes the ``llm`` decision to
        # ``run_llm_path`` instead of degrading. ``None`` keeps the first-release
        # behaviour (degrade with ``llm_not_implemented_mvp0``).
        self._engine = engine
        self._indexer = indexer
        self._llm_client = llm_client

    def ingest(
        self,
        payload: RememberPayload,
        extraction_mode: ExtractionMode,
        *,
        now: datetime | None = None,
    ) -> WriteResult:
        decision = decide_path(payload, extraction_mode)
        if decision.path == "llm":
            if self._llm_client is not None:
                # Lazy import avoids the stub↔llm cycle (llm.py imports
                # _degrade_to_skip from here).
                from seahorse.write_path.llm import run_llm_path

                result = run_llm_path(
                    payload, decision, self._engine, self._llm_client, now=now
                )
            else:
                result = _degrade_to_skip(payload, decision, self._engine, now=now)
        else:
            result = run_skip_path(payload, decision, self._engine, now=now)
        if (
            self._indexer is not None
            and result.ep_id is not None
            and result.status == "ACTIVE"
        ):
            self._indexer.index_episode(result.ep_id)
        return result


__all__ = ["StubWritePath", "run_skip_path", "_degrade_to_skip"]