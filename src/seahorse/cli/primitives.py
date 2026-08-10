"""Memory-native primitive commands for the CLI (#14).

The logic layer of the 6 primitives (``remember`` / ``recall`` /
``recall-timeline`` / ``recall-full`` / ``improve`` / ``forget``) plus the
``expire`` / ``revalidate`` stubs (SO-14-05). These functions are
parser-agnostic: ``app.py`` wires Typer callbacks that parse argv and call
them; tests call them directly with a ``MemoryFacade`` (real via
``build_facade`` or a ``RecordingFacade`` double).

Delegation purity (f5-14 §1, load-bearing):
- #14 is a CLIENT of #12. It calls the facade only — never #2/#5/#8/#11.
- CLI-shape validation is limited to: size caps (constants.py), vocabulary
  membership the facade does NOT enforce (``source_type`` / ``cognitive_type``),
  and ISO-8601 parsing. Facade-owned checks (empty body/query, missing
  source_type, invalid extraction_mode, PIT kind/t) are LEFT to surface as Cat A
  exit codes (64+) — #14 does not invent ``SeahorseError`` codes (f5-14 §3.3).
- ``list[IndexRow]`` always (shape stable MVP-0 → MVP-1). ``--pit`` on recall
  INDEX → ``E_PIT_RECALL_MVP_0`` (70) from the facade, not intercepted.
- ``--tag`` is NOT exposed in MVP-0 (facade rejects non-empty tags with
  ``E_NOT_IN_MVP_0_1``; ADR-10 honesty + YAGNI — tags are MVP-1).
- ``now`` IS exposed on ``forget`` (f5-13 §9.2: CLI is where the clock override
  lives for tests / internal CLI; backdating risk is bounded to the local
  surface, unlike #13 MCP which hides it).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TextIO

from seahorse.cli.errors import CliNotInMVP0, CliUsageError
from seahorse.cli.output import (
    OutputFormat,
    render_episode,
    render_full_details,
    render_index_rows,
    render_timeline,
    render_write_result,
)
from seahorse.constants import (
    BODY_MAX_CHARS,
    COGNITIVE_TYPES,
    EP_ID_MAX_CHARS,
    PROVENANCE_ID_MAX_CHARS,
    QUERY_MAX_CHARS,
    REASON_MAX_CHARS,
    SOURCE_TYPES,
    SUBJECT_FILTER_MAX_CHARS,
)
from seahorse.disclosure.types import SUMMARY_MAX_CHARS
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import Provenance, RememberPayload

# ---------------------------------------------------------------------------
# CLI-shape helpers (caps + vocabulary + datetime parsing).
# ---------------------------------------------------------------------------


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
    membership — #14 guards the vocabulary at the CLI border so garbage does
    not get stored verbatim by the engine). ``agent_id``/``session_id`` are
    length-capped; they are NOT required for ``source_type=agent`` in MVP-0
    (the facade/engine do not enforce it — documented future enhancement).
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


# ---------------------------------------------------------------------------
# remember
# ---------------------------------------------------------------------------


def run_remember(
    facade: MemoryFacade,
    *,
    body: str,
    source_type: str = "human",
    agent_id: str | None = None,
    session_id: str | None = None,
    valid_at: str | None = None,
    cognitive_type: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    skip_extraction: bool | None = None,
    extraction_mode: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse remember`` — clean creation via #5 ``ingest`` (ADR-09).

    ``extraction_mode`` validation is intentionally DEFERRED to the facade — it
    owns ``E_INVALID_EXTRACTION_MODE`` (66). #14 does not replicate mode
    validation (delegation purity, f5-14 §3.3); a typo like ``llm_partial``
    surfaces as Cat A exit 66, not a CLI usage error. ``summary`` is an additive
    editorial field (OQ3 enabler): when omitted, the write path derives a
    deterministic fallback (first sentence of the body).
    """
    _require_le(body, limit=BODY_MAX_CHARS, field="body")
    _validate_cognitive_type(cognitive_type)
    by = _build_provenance(source_type=source_type, agent_id=agent_id, session_id=session_id)
    va = _parse_dt(valid_at, field="valid-at") if valid_at is not None else None
    if title is not None:
        _require_le(title, limit=SUBJECT_FILTER_MAX_CHARS, field="title")
    if summary is not None:
        _require_le(summary, limit=SUMMARY_MAX_CHARS, field="summary")

    payload = RememberPayload(
        body=body,
        by=by,
        valid_at=va,
        cognitive_type=cognitive_type,
        title=title,
        summary=summary,
    )
    result = facade.remember(
        payload,
        skip_extraction=skip_extraction,
        extraction_mode=extraction_mode,  # type: ignore[arg-type]
    )
    render_write_result(result, fmt, out)


# ---------------------------------------------------------------------------
# recall (INDEX)
# ---------------------------------------------------------------------------


def run_recall(
    facade: MemoryFacade,
    *,
    query: str,
    top_k: int = 10,
    cognitive_type: str | None = None,
    subject_filter: str | None = None,
    pit_kind: str | None = None,
    pit_t: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse recall`` — INDEX level (MVP-0 G2 vigente listing)."""
    _require_le(query, limit=QUERY_MAX_CHARS, field="query")
    _validate_cognitive_type(cognitive_type)
    if subject_filter is not None:
        _require_le(subject_filter, limit=SUBJECT_FILTER_MAX_CHARS, field="subject-filter")

    pit = None
    if pit_kind is not None:
        t = _parse_dt(pit_t, field="pit-t") if pit_t is not None else None
        # build_pit may raise E_PIT_REQUIRES_T (69) / E_INVALID_PIT_KIND (68).
        pit = facade.build_pit(pit_kind=pit_kind, t=t)

    # Facade validates empty query (E_EMPTY_QUERY=67) and pit on INDEX
    # (E_PIT_RECALL_MVP_0=70). #14 does NOT intercept — those are Cat A.
    #
    # Forward optional filters only when present (sister-projection parity with
    # #13: absent keys are ABSENT in the facade call, not collapsed to None —
    # a structural delegation-purity invariant, not a behavioral difference).
    recall_kwargs: dict[str, Any] = {"pit": pit, "k": top_k}
    if cognitive_type is not None:
        recall_kwargs["cognitive_type"] = cognitive_type
    if subject_filter is not None:
        recall_kwargs["subject_filter"] = subject_filter
    rows = facade.recall(query, **recall_kwargs)
    render_index_rows(rows, fmt=fmt, out=out, query=query)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def run_context(
    facade: MemoryFacade,
    *,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse context`` — the memory bootstrap (SessionStart hook, §6.3).

    Renders the four INDEX-level blocks (recent / vigente / last session /
    header + counter + pointer) via the context assembler. The hook calls the
    CLI which calls the facade — the facade's ``context()`` is the single point
    of change (obsiforge §6.3). Degrades to "no context" when the DB is empty.
    """
    from seahorse.context.assembler import render_context

    data = facade.context()
    out.write(render_context(data) + "\n")


# ---------------------------------------------------------------------------
# recall-timeline
# ---------------------------------------------------------------------------


def run_recall_timeline(
    facade: MemoryFacade,
    *,
    anchor_ep_id: str,
    axis: str = "supersedes_chain",
    pit_kind: str | None = None,
    pit_t: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse recall-timeline`` — TIMELINE level (anchor-based, no body)."""

    _require_le(anchor_ep_id, limit=EP_ID_MAX_CHARS, field="anchor-ep-id")

    pit = None
    if pit_kind is not None:
        t = _parse_dt(pit_t, field="pit-t") if pit_t is not None else None
        pit = facade.build_pit(pit_kind=pit_kind, t=t)

    window = facade.recall_timeline(anchor_ep_id, axis=axis, pit=pit)
    render_timeline(window, fmt=fmt, out=out)


# ---------------------------------------------------------------------------
# recall-full
# ---------------------------------------------------------------------------


def run_recall_full(
    facade: MemoryFacade,
    *,
    ep_ids: list[str],
    pit_kind: str | None = None,
    pit_t: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse recall-full`` — FULL level (hydrates body, cap MAX_FULL_BATCH=5).

    The batch cap (``FullBatchTooLarge`` → 84) and PIT-in-full refusal
    (``PitFullNotSupported`` → 85) are #8's domain contract — #14 does NOT
    replicate them (delegation purity, f5-14 §1).
    """

    for ep_id in ep_ids:
        _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")

    pit = None
    if pit_kind is not None:
        t = _parse_dt(pit_t, field="pit-t") if pit_t is not None else None
        pit = facade.build_pit(pit_kind=pit_kind, t=t)

    details = facade.recall_full(ep_ids, pit=pit)
    render_full_details(details, fmt=fmt, out=out)


# ---------------------------------------------------------------------------
# improve
# ---------------------------------------------------------------------------


def run_improve(
    facade: MemoryFacade,
    *,
    ep_id: str,
    new_body: str,
    reason: str = "correction",
    source_type: str = "human",
    agent_id: str | None = None,
    valid_at: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse improve`` — editorial correction (invalidate + append)."""

    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    _require_le(new_body, limit=BODY_MAX_CHARS, field="new-body")
    _require_le(reason, limit=REASON_MAX_CHARS, field="reason")
    by = _build_provenance(source_type=source_type, agent_id=agent_id, session_id=None)
    va = _parse_dt(valid_at, field="valid-at") if valid_at is not None else None

    episode = facade.improve(ep_id, new_body, by=by, valid_at=va, reason=reason)
    render_episode(episode, fmt=fmt, out=out, verb="Improved")


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def run_forget(
    facade: MemoryFacade,
    *,
    ep_id: str,
    reason: str,
    source_type: str = "human",
    agent_id: str | None = None,
    now: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse forget`` — bi-temporal soft-delete (ADR-07).

    ``now`` IS exposed (f5-13 §9.2): the CLI is the local surface where the
    clock override lives for tests / internal use, unlike #13 MCP (backdating
    risk bounds it to the local actor).
    """

    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    _require_le(reason, limit=REASON_MAX_CHARS, field="reason")
    by = _build_provenance(source_type=source_type, agent_id=agent_id, session_id=None)
    now_dt = _parse_dt(now, field="now") if now is not None else None

    episode = facade.forget(ep_id, reason=reason, by=by, now=now_dt)
    render_episode(episode, fmt=fmt, out=out, verb="Forgotten")


# ---------------------------------------------------------------------------
# expire / revalidate — SO-14-05: CLI-intercepted, Cat C CLI_NOT_IN_MVP_0 (75).
# ---------------------------------------------------------------------------


def run_expire_revalidate(command: str) -> None:
    """Refuse ``expire`` / ``revalidate`` at the CLI layer (SO-14-05).

    Never reaches ``facade.expire``/``facade.revalidate`` (which raise
    ``E_NOT_IN_MVP_0_1``). The CLI owns the "reserved command" surface so the
    user sees the command exists and is reserved, not absent (fail-loud,
    divergent from #13 MCP which omits the tools entirely — honest divergence).
    """
    raise CliNotInMVP0(command, reason="decay/revalidate are MVP-1+ (mediano)")


__all__ = [
    "run_remember",
    "run_recall",
    "run_recall_timeline",
    "run_recall_full",
    "run_improve",
    "run_forget",
    "run_expire_revalidate",
]