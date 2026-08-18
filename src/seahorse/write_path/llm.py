"""``run_llm_path`` — the real LLM extraction path.

Over the honest extension point: ``StubWritePath.ingest`` degrades
``llm``→``skip`` when no client is wired; when a real ``LLMClient`` is present
it delegates here instead of degrading. Every failure mode degrades HONESTLY:
provenance core carries the corrected ``extraction_mode='skip'`` with the
durable ``degraded_from`` / ``degrade_reason`` marker, and the caller's LLM
intent is logged, never stored (``stub._degrade_to_skip`` handles all of that).

How the design maps to the code:

- ``payload`` is a ``RememberPayload`` (not a dict); ``_degrade_to_skip`` lives
  in ``stub.py`` and already threads the degrade marker with a custom ``reason``.
- The extractor-produced ``subject`` is passed to ``engine.remember`` via the
  subject override; ``valid_at`` / ``cognitive_type`` fall back to the payload's
  when the model omits them (pass-through — the caller's explicit values win).
- LLM-extracted ``tags`` are validated by ``EpisodeFrontmatter`` but NOT passed
  to ``remember``: the SQLite episode store does not persist ``tags`` yet (see
  ``engine.remember`` docstring); the schema keeps them so the model may emit
  them without ``extra="forbid"`` rejecting the episode.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from seahorse.constants import COGNITIVE_TYPES
from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import RememberPayload
from seahorse.llm import BudgetContext, LLMClient
from seahorse.llm.types import EPISODE_COST_CAP_USD
from seahorse.write_path.decide import PathDecision
from seahorse.write_path.extract import derive_summary
from seahorse.write_path.stub import _degrade_to_skip, _EngineLike

_logger = logging.getLogger("seahorse.write_path.llm")


class EpisodeFrontmatter(BaseModel):
    """LLM-produced editorial frontmatter (strict ``extra="forbid"``).

    The schema_hint the extractor validates against. ``extra="forbid"`` makes a
    hallucinated field a validation error (triggering the extractor's repair
    prompt) instead of silent garbage. Only editorial fields the write path can
    use: the engine owns the timestamps and ``id``; ``schema_version`` stays the
    caller's.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str  # REQUIRED — the point of the extraction
    valid_at: datetime | None = None
    cognitive_type: str | None = None
    tags: list[str] = []

    @field_validator("subject")
    @classmethod
    def _subject_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("subject is required — derive a short topic phrase")
        return v

    @field_validator("valid_at")
    @classmethod
    def _aware_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("valid_at must be timezone-aware (naive datetimes rejected)")
        return v

    @field_validator("cognitive_type")
    @classmethod
    def _known_cognitive_type(cls, v: str | None) -> str | None:
        if v is not None and v not in COGNITIVE_TYPES:
            raise ValueError(f"unknown cognitive_type: {v!r}")
        return v


def run_llm_path(
    payload: RememberPayload,
    decision: PathDecision,
    engine: _EngineLike,
    llm_client: LLMClient,
    *,
    now: datetime | None = None,
) -> WriteResult:
    """Execute the LLM extraction path.

    1. Call ``llm_client.extract`` (the caller's ``BudgetContext`` is created
       here, per episode — the LLM client is stateless between episodes).
    2. Every failure degrades to skip with a distinct reason: the initial stub
       (``NotImplementedError``), any backend exception, the extractor's own
       ``degraded_to_skip``, or a final schema drift (``final_validation_failed``).
    3. On success, build the EFFECTIVE provenance (``extraction_mode='llm'`` +
       the extractor's ``model_used`` / ``prompt_hash`` / ``confidence``) and
       delegate to ``engine.remember`` with the validated fields.
    """
    budget = BudgetContext(cap_usd=EPISODE_COST_CAP_USD)  # near-zero-cost: ≤ $0.002/episode
    try:
        result = llm_client.extract(
            content=payload.body,
            schema_hint=EpisodeFrontmatter,
            budget=budget,
        )
    except NotImplementedError:
        # Initial stub: degrade with the canonical reason (no crash to the caller).
        _logger.warning("llm_path.stub_mvp0 -> skip")
        return _degrade_to_skip(
            payload, decision, engine, now=now, reason="llm_not_implemented_mvp0"
        )
    except Exception as exc:  # noqa: BLE001 — any backend failure degrades
        _logger.error("llm_path.extract_failed: %s", exc)
        return _degrade_to_skip(
            payload, decision, engine, now=now, reason="llm_exception"
        )
    if result.degraded_to_skip:
        # The LLM client exhausted its chain/budget and already flipped to skip.
        _logger.warning(
            "llm_path.degraded_to_skip model=%s cost_usd=%s",
            result.model_used,
            result.cost_usd,
        )
        return _degrade_to_skip(
            payload, decision, engine, now=now, reason="llm_degraded"
        )
    try:
        validated = EpisodeFrontmatter.model_validate(result.data)
    except ValidationError:
        # Drift guard: the LLM client validated with extra=forbid, but this
        # final check protects against schema drift between the LLM client and
        # the write path.
        _logger.warning("llm_path.final_validation_fail")
        return _degrade_to_skip(
            payload, decision, engine, now=now, reason="final_validation_failed"
        )

    by: dict[str, Any] = {
        **payload.by,
        "extraction_mode": "llm",  # effective, not degraded
        "model_used": result.model_used,  # effective, with tag/version
        "prompt_hash": result.prompt_hash,  # SHA-256 of the effective prompt
        "confidence": result.confidence,  # float[0,1] | None
    }
    # cost_usd is logged, NOT stored in provenance core.
    _logger.info(
        "llm_path.cost x-seahorse-cost-usd=%s model_used=%s tokens_spent=%s",
        result.cost_usd,
        result.model_used,
        budget.tokens_spent,
    )
    return engine.remember(
        body=payload.body,
        by=by,
        valid_at=(
            validated.valid_at if validated.valid_at is not None else payload.valid_at
        ),
        cognitive_type=(
            validated.cognitive_type
            if validated.cognitive_type is not None
            else payload.cognitive_type
        ),
        schema_version=payload.schema_version,
        title=payload.title,
        summary=payload.summary or derive_summary(payload.body),  # deterministic fallback
        subject=validated.subject,  # None → engine derives the subject
        now=now,
    )


__all__ = ["EpisodeFrontmatter", "run_llm_path"]
