"""Memory-native primitive commands for the CLI.

The logic layer of the 6 primitives (``remember`` / ``recall`` /
``recall-timeline`` / ``recall-full`` / ``improve`` / ``forget``) plus the
``expire`` / ``revalidate`` stubs. These functions are parser-agnostic:
``app.py`` wires Typer callbacks that parse argv and call them; tests call them
directly with a ``MemoryFacade`` (real via ``build_facade`` or a
``RecordingFacade`` double).

Delegation purity (load-bearing):
- The CLI is a CLIENT of the facade. It calls the facade only — never the
  engine, the write path, the disclosure layer, or the retrieval layer.
- CLI-shape validation is limited to: size caps (constants.py), vocabulary
  membership the facade does NOT enforce (``source_type`` / ``cognitive_type``),
  and ISO-8601 parsing. Facade-owned checks (empty body/query, missing
  source_type, invalid extraction_mode, PIT kind/t) are LEFT to surface as Cat A
  exit codes (64+) — the CLI does not invent ``SeahorseError`` codes.
- ``list[IndexRow]`` always (shape stable across releases). ``--pit`` on recall
  INDEX → ``E_PIT_RECALL_MVP_0`` (70) from the facade, not intercepted.
- ``--tag`` is NOT exposed in the current release (facade rejects non-empty
  tags with ``E_NOT_IN_MVP_0_1``; fail-loud honesty + YAGNI — tags are a later
  release).
- ``now`` IS exposed on ``forget``: the CLI is where the clock override lives
  for tests / internal use; backdating risk is bounded to the local surface,
  unlike the MCP server which hides it.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, TypeVar

from seahorse.cli.config import SeahorseConfig
from seahorse.cli.errors import CliNotInMVP0, CliUsageError
from seahorse.cli.output import (
    OutputFormat,
    render_audit_log,
    render_episode,
    render_freshness_view,
    render_full_details,
    render_index_rows,
    render_supersedes_chain,
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
from seahorse.llm import LLMClient

# ---------------------------------------------------------------------------
# CLI-shape helpers (caps + vocabulary + datetime parsing).
# ---------------------------------------------------------------------------

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
    verbose: bool = False,
) -> None:
    """``seahorse remember`` — clean creation via the write path (near-zero cost).

    ``extraction_mode`` validation is intentionally DEFERRED to the facade — it
    owns ``E_INVALID_EXTRACTION_MODE`` (66). The CLI does not replicate mode
    validation (delegation purity); a typo like ``llm_partial`` surfaces as Cat
    A exit 66, not a CLI usage error. ``summary`` is an additive editorial
    field: when omitted, the write path derives a deterministic fallback
    (first sentence of the body).
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
    result = _timed(
        "remember",
        lambda: facade.remember(
            payload,
            skip_extraction=skip_extraction,
            extraction_mode=extraction_mode,  # type: ignore[arg-type]
        ),
        verbose=verbose,
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
    verbose: bool = False,
) -> None:
    """``seahorse recall`` — INDEX level (current-state listing)."""
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
    # (E_PIT_RECALL_MVP_0=70). The CLI does NOT intercept — those are Cat A.
    #
    # Forward optional filters only when present (parity with the MCP server:
    # absent keys are ABSENT in the facade call, not collapsed to None — a
    # structural delegation-purity invariant, not a behavioral difference).
    recall_kwargs: dict[str, Any] = {"pit": pit, "k": top_k}
    if cognitive_type is not None:
        recall_kwargs["cognitive_type"] = cognitive_type
    if subject_filter is not None:
        recall_kwargs["subject_filter"] = subject_filter
    rows = _timed(
        "recall", lambda: facade.recall(query, **recall_kwargs), verbose=verbose
    )
    render_index_rows(rows, fmt=fmt, out=out, query=query)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def run_context(
    facade: MemoryFacade,
    *,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse context`` — the memory bootstrap (SessionStart hook).

    Renders the four INDEX-level blocks (recent / current-state / last session
    / header + counter + pointer) via the context assembler. The hook calls the
    CLI which calls the facade — the facade's ``context()`` is the single point
    of change. Degrades to "no context" when the DB is empty.
    """
    from seahorse.context.assembler import render_context

    data = _timed("context", facade.context, verbose=verbose)
    out.write(render_context(data) + "\n")


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------


def _vault_human_edited(vault_path: Path | None) -> Callable[[Any], bool] | None:
    """The editorial-authority predicate: a note whose vault ``.md`` was edited
    after its creation is human-touched → never superseded.

    Scans the vault once (clustering key → ``.md`` mtime + frontmatter id) and
    returns a predicate over an existing note. ``None`` when no vault path is
    available (the supersession proceeds without the human-edit guard).

    The frontmatter id is the C3 guard: a ``.md`` whose id matches the note's
    id is seahorse's OWN materialization (written after ``created_at``, so its
    mtime is always newer) — never a human edit. Only a ``.md`` with a
    DIFFERENT or absent id (a human-authored note) can be human-touched.
    """
    if vault_path is None:
        return None
    from seahorse.distill.cluster import cluster_key

    key_to_entries: dict[str, list[tuple[float, str | None]]] = {}
    for path in vault_path.rglob("*.md"):
        try:
            subject = _md_subject(path)
        except OSError:
            continue
        if subject:
            key_to_entries.setdefault(cluster_key(subject), []).append(
                (path.stat().st_mtime, _md_frontmatter_id(path))
            )

    def _human_edited(note: Any) -> bool:
        entries = key_to_entries.get(cluster_key(note.subject or ""))
        if not entries:
            return False  # no vault .md for this note → not human-touched
        created = note.created_at
        if created is None:
            return False
        for mtime, md_id in entries:
            if md_id == note.id:
                continue  # seahorse's own materialization — not a human edit (C3)
            if mtime > created.timestamp():
                return True  # a human-authored .md edited after the note's creation
        return False

    return _human_edited


def _md_subject(path: Path) -> str:
    """The subject of a vault ``.md``: the first H1 (no frontmatter parsing).

    The consolidated notes are written with ``# {clustering key}`` as H1, so the
    H1 extraction matches the note's subject without parsing frontmatter.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _md_frontmatter_id(path: Path) -> str | None:
    """The frontmatter ``id`` of a vault ``.md``, or None when absent.

    A light parse (no ruamel): the F3.1 frontmatter is a YAML block between
    ``---`` fences with ``id: <uuid>`` as a top-level key. A note whose id
    matches the episode id is seahorse's own materialization (C3); a note with
    a different or absent id is human-authored.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    for line in text.splitlines()[1:]:
        if line == "---":
            break
        if line.startswith("id:"):
            return line[3:].strip().strip("'\"")
    return None


def run_consolidate(
    facade: MemoryFacade,
    *,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
    synthesis: str = "skip",
    llm_client: LLMClient | None = None,
    vault_path: Path | None = None,
    supersede: bool = False,
) -> None:
    """``seahorse consolidate`` — distill recurrent episodes.

    Reads the current-state set, clusters by subject recurrence (N≥3), and
    distills each cluster into a consolidated semantic knowledge note.
    Idempotent: a cluster whose key already has a consolidated note is skipped.
    ``synthesis="llm"`` (with a wired ``llm_client``) adds the off-path LLM
    synthesis; a failed synthesis degrades honestly to the deterministic body.
    ``supersede=True`` (F7+ supersession, opt-in) UPDATES an existing note when
    the cluster gains new episodes, guarded by the editorial authority
    (``vault_path`` mtime check — a human-edited note is never superseded).
    """
    from seahorse.distill.consolidate import consolidate

    report = _timed(
        "consolidate",
        lambda: consolidate(
            facade,
            synthesis=synthesis,
            llm_client=llm_client,
            supersede=supersede,
            human_edited=_vault_human_edited(vault_path),
        ),
        verbose=verbose,
    )
    if fmt == "human":
        if report.items:
            for item in report.items:
                suffix = ""
                if item.synthesis == "llm":
                    suffix = " [llm]"
                elif item.synthesis == "degraded":
                    suffix = " [degraded]"
                out.write(
                    f"consolidated: {item.key} ({item.source_count} sources) "
                    f"-> {item.status}{suffix}\n"
                )
        else:
            out.write(f"consolidate: no clusters to distill ({report.clusters_found} found)\n")
    else:
        import json

        out.write(
            json.dumps(
                {
                    "clusters_found": report.clusters_found,
                    "items": [
                        {
                            "key": i.key,
                            "source_count": i.source_count,
                            "status": i.status,
                            "ep_id": i.ep_id,
                            "synthesis": i.synthesis,
                        }
                        for i in report.items
                    ],
                },
                ensure_ascii=False,
            )
            + "\n"
        )


# ---------------------------------------------------------------------------
# materialize
# ---------------------------------------------------------------------------


def run_materialize(
    config: SeahorseConfig,
    *,
    mode: str | None = None,
    cognitive_type: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse materialize`` — backfill .md notes for currently-valid episodes.

    Reads the current-state set (the ``get_vigente`` predicate — M7: PENDING
    episodes are not materialized until vigente) and materializes each as an
    F3.1 note in the configured ``[materialize] dir``. ``mode`` overrides the
    config's mode (consolidated|all|off); ``cognitive_type`` filters the set.
    Idempotent: already-materialized notes are skipped (frontmatter-id guard,
    C3). Best-effort per note (M9): a failed write is reported, never fatal.

    Unlike the primitive commands this takes the ``SeahorseConfig`` (not the
    facade): the backfill needs the vault root + ``[materialize]`` section +
    the sidecar, which the facade does not expose. It builds its own Storage +
    engine (the ``run_index_rebuild`` pattern) and closes them in ``finally``.
    """
    from seahorse.cli.errors import CliMaterializeNotConfigured
    from seahorse.engine.engine import BiTemporalEngine
    from seahorse.frontmatter.materialize import Materializer
    from seahorse.persistence.storage import Storage

    mcfg = config.materialize
    if mcfg is None:
        raise CliMaterializeNotConfigured()
    effective_mode = mode or mcfg.mode
    storage = Storage(config.db_path)
    try:
        engine = BiTemporalEngine(repo=storage.episodes, audit=storage.audit)
        eps = engine.get_vigente(None, now=datetime.now(UTC))
        if cognitive_type is not None:
            eps = [e for e in eps if e.cognitive_type == cognitive_type]
        materializer = Materializer(
            config.vault, dir=mcfg.dir, sidecar=storage.sidecar, mode=effective_mode
        )
        report = _timed(
            "materialize",
            lambda: materializer.materialize_episodes(eps),
            verbose=verbose,
        )
    finally:
        storage.close()
    if fmt == "human":
        if report.items:
            for item in report.items:
                if item.status == "written":
                    out.write(f"materialized: {item.ep_id} -> {item.path}\n")
                elif item.status == "skipped":
                    out.write(f"skipped: {item.ep_id} ({item.reason})\n")
                elif item.status == "collision":
                    out.write(f"collision: {item.ep_id} ({item.reason})\n")
                else:
                    out.write(f"error: {item.ep_id} ({item.reason})\n")
        else:
            out.write("materialize: no currently-valid episodes to materialize\n")
    else:
        import json

        out.write(
            json.dumps(
                {
                    "written": report.written,
                    "skipped": report.skipped,
                    "items": [
                        {
                            "ep_id": i.ep_id,
                            "status": i.status,
                            "path": i.path,
                            "reason": i.reason,
                        }
                        for i in report.items
                    ],
                },
                ensure_ascii=False,
            )
            + "\n"
        )


# ---------------------------------------------------------------------------
# recall-timeline
# ---------------------------------------------------------------------------


def run_recall_timeline(
    facade: MemoryFacade,
    *,
    anchor_ep_id: str,
    axis: str = "supersedes_chain",
    hops: int = 1,
    pit_kind: str | None = None,
    pit_t: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse recall-timeline`` — TIMELINE level (anchor-based, no body).

    ``hops`` is the ``graph_bfs`` traversal depth (1-2; >2 surfaces the
    disclosure layer's ``HopsCapExceeded`` — the CLI does NOT replicate the
    cap, delegation purity).
    """

    _require_le(anchor_ep_id, limit=EP_ID_MAX_CHARS, field="anchor-ep-id")

    pit = None
    if pit_kind is not None:
        t = _parse_dt(pit_t, field="pit-t") if pit_t is not None else None
        pit = facade.build_pit(pit_kind=pit_kind, t=t)

    window = _timed(
        "recall_timeline",
        lambda: facade.recall_timeline(anchor_ep_id, axis=axis, hops=hops, pit=pit),
        verbose=verbose,
    )
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
    verbose: bool = False,
) -> None:
    """``seahorse recall-full`` — FULL level (hydrates body, cap MAX_FULL_BATCH=5).

    The batch cap (``FullBatchTooLarge`` → 84) and PIT-in-full refusal
    (``PitFullNotSupported`` → 85) are the disclosure layer's domain contract —
    the CLI does NOT replicate them (delegation purity).
    """

    for ep_id in ep_ids:
        _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")

    pit = None
    if pit_kind is not None:
        t = _parse_dt(pit_t, field="pit-t") if pit_t is not None else None
        pit = facade.build_pit(pit_kind=pit_kind, t=t)

    details = _timed(
        "recall_full", lambda: facade.recall_full(ep_ids, pit=pit), verbose=verbose
    )
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
    verbose: bool = False,
) -> None:
    """``seahorse improve`` — editorial correction (invalidate + append)."""

    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    _require_le(new_body, limit=BODY_MAX_CHARS, field="new-body")
    _require_le(reason, limit=REASON_MAX_CHARS, field="reason")
    by = _build_provenance(source_type=source_type, agent_id=agent_id, session_id=None)
    va = _parse_dt(valid_at, field="valid-at") if valid_at is not None else None

    episode = _timed(
        "improve",
        lambda: facade.improve(ep_id, new_body, by=by, valid_at=va, reason=reason),
        verbose=verbose,
    )
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
    verbose: bool = False,
) -> None:
    """``seahorse forget`` — bi-temporal soft-delete.

    ``now`` IS exposed: the CLI is the local surface where the clock override
    lives for tests / internal use, unlike the MCP server (backdating risk
    bounds it to the local actor).
    """

    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    _require_le(reason, limit=REASON_MAX_CHARS, field="reason")
    by = _build_provenance(source_type=source_type, agent_id=agent_id, session_id=None)
    now_dt = _parse_dt(now, field="now") if now is not None else None

    episode = _timed(
        "forget",
        lambda: facade.forget(ep_id, reason=reason, by=by, now=now_dt),
        verbose=verbose,
    )
    render_episode(episode, fmt=fmt, out=out, verb="Forgotten")


# ---------------------------------------------------------------------------
# freshness-view / audit-log / follow-supersedes-chain — read-only facade tools.
# ---------------------------------------------------------------------------


def run_freshness_view(
    facade: MemoryFacade,
    *,
    ep_id: str,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse freshness-view`` — freshness snapshot of an episode."""
    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    view = _timed(
        "freshness_view", lambda: facade.freshness_view(ep_id), verbose=verbose
    )
    render_freshness_view(view, fmt=fmt, out=out)


def run_audit_log(
    facade: MemoryFacade,
    *,
    ep_id: str,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse audit-log`` — the write-path history of an episode."""
    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    events = _timed("audit_log", lambda: facade.audit_log(ep_id), verbose=verbose)
    render_audit_log(events, fmt=fmt, out=out)


def run_follow_supersedes_chain(
    facade: MemoryFacade,
    *,
    ep_id: str,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse follow-supersedes-chain`` — the version history of an episode."""
    _require_le(ep_id, limit=EP_ID_MAX_CHARS, field="ep-id")
    episodes = _timed(
        "follow_supersedes_chain",
        lambda: facade.follow_supersedes_chain(ep_id),
        verbose=verbose,
    )
    render_supersedes_chain(episodes, fmt=fmt, out=out)


# ---------------------------------------------------------------------------
# expire / revalidate — CLI-intercepted, Cat C CLI_NOT_IN_MVP_0 (75).
# ---------------------------------------------------------------------------


def run_expire_revalidate(command: str) -> None:
    """Refuse ``expire`` / ``revalidate`` at the CLI layer.

    Never reaches ``facade.expire``/``facade.revalidate`` (which raise
    ``E_NOT_IN_MVP_0_1``). The CLI owns the "reserved command" surface so the
    user sees the command exists and is reserved, not absent (fail-loud,
    divergent from the MCP server which omits the tools entirely — honest
    divergence).
    """
    raise CliNotInMVP0(command, reason="decay/revalidate are a later release (medium-term goal)")


__all__ = [
    "run_remember",
    "run_recall",
    "run_recall_timeline",
    "run_recall_full",
    "run_improve",
    "run_forget",
    "run_freshness_view",
    "run_audit_log",
    "run_follow_supersedes_chain",
    "run_expire_revalidate",
]