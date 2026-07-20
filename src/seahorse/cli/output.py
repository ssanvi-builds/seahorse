"""Output formatting for the CLI (#14) — human / json / jsonl.

Three output formats (f5-14 §3.4):
- **human** (default): plain-text tables/tree, no ANSI — deterministic for
  tests and piping. (Rich tables are a future enhancement; plain text keeps
  output stable and grep-able.)
- **json**: canonical JSON over the F3.1 shape, ``exclude_none=False`` (nulls
  explicit, shape stable MVP-0 → MVP-1, parity with #13's wire serializer).
- **jsonl**: one JSON object per line, for streams (``recall``/``recall-full``).

Serialization policy mirrors #13's ``seahorse.mcp.serialize`` (datetime →
ISO-8601 UTC with ``Z``, ``exclude_none=False``, dataclass → field dict,
tuple/set → array) but is OWNED by #14 — the sister projections are
independently disposable (f5-14 §1.1: if MCP fragments, #14 stays operational),
so the CLI does not import the MCP serializer.

Honest MVP-0 gap (f5-14 §2.2 vs real facade): ``facade.remember`` returns
``WriteResult`` (``ep_id``/``fact_id``/``status``/``collisions_detected``), NOT
the full ``Episode``. The f5-14 ``--json`` example showed the episode embedded;
#14 cannot fetch it without bypassing the facade (the only domain seam), so
``remember`` outputs the ``WriteResult`` honestly. ``improve``/``forget`` return
``Episode`` and render it in full.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TextIO
from uuid import UUID

from seahorse.contracts.engine import WriteResult
from seahorse.contracts.episode import Episode
from seahorse.facade import FullDetail, IndexRow, TimelineWindow

OutputFormat = Literal["human", "json", "jsonl"]


def _iso_z(dt: datetime) -> str:
    """Canonicalize a datetime to UTC ISO-8601 with a ``Z`` suffix (F3.1 §4)."""
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def to_jsonable(obj: Any) -> Any:
    """Recursively convert a Python object to a JSON-able value (mirrors #13)."""
    if obj is None:
        return None
    if isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, datetime):
        return _iso_z(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, UUID):
        # Defensive parity with #13's to_wire: id fields are str today, but a
        # UUID slipping through serializes canonically rather than falling to
        # the default ``str(obj)`` tail (which yields the same, but explicit).
        return str(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (tuple, list, set, frozenset)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    return obj


def to_json(obj: Any) -> str:
    """Serialize to a compact JSON string (nulls explicit)."""
    return json.dumps(to_jsonable(obj), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Human-format helpers (plain text, deterministic).
# ---------------------------------------------------------------------------


def _fmt_dt(dt: datetime | None) -> str:
    return _iso_z(dt) if dt is not None else "null"


def _truncate(text: str, limit: int = 48) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Per-result renderers. Each writes to ``out`` and returns None.
# ---------------------------------------------------------------------------


def render_write_result(result: WriteResult, fmt: OutputFormat, out: TextIO) -> None:
    """``remember`` output: ``WriteResult`` (no Episode in MVP-0)."""
    if fmt == "json":
        out.write(to_json(result) + "\n")
        return
    if fmt == "jsonl":
        out.write(to_json(result) + "\n")
        return
    # human
    out.write("✓ Remembered\n")
    out.write(f"  fact_id:    {result.fact_id or '-'}\n")
    out.write(f"  ep_id:      {result.ep_id or '-'}\n")
    out.write(f"  status:     {result.status}\n")
    out.write(f"  collisions: {len(result.collisions_detected)}\n")


def render_episode(
    result: Episode, *, fmt: OutputFormat, out: TextIO, verb: str
) -> None:
    """``improve`` / ``forget`` output: the returned ``Episode``."""
    if fmt in ("json", "jsonl"):
        out.write(to_json(result) + "\n")
        return
    out.write(f"✓ {verb}\n")
    out.write(f"  ep_id:        {result.id}\n")
    out.write(f"  fact_id:      {result.fact_id or '-'}\n")
    out.write(f"  subject:      {_truncate(result.subject or '-')}\n")
    out.write(f"  status:       {'invalidated' if result.invalid_at else 'vigente'}\n")
    out.write(f"  valid_at:     {_fmt_dt(result.valid_at)}\n")
    out.write(f"  invalid_at:   {_fmt_dt(result.invalid_at)}\n")
    out.write(f"  created_at:   {_fmt_dt(result.created_at)}\n")
    if result.supersedes:
        out.write(f"  supersedes:   {result.supersedes}\n")
    out.write(f"  cognitive:    {result.cognitive_type or '-'}\n")


def render_index_rows(
    rows: list[IndexRow], *, fmt: OutputFormat, out: TextIO, query: str
) -> None:
    """``recall`` output: ``list[IndexRow]``."""
    if fmt == "json":
        out.write(to_json(rows) + "\n")
        return
    if fmt == "jsonl":
        for row in rows:
            out.write(to_json(row) + "\n")
        return
    # human
    out.write(f'Recall: {query!r} ({len(rows)} results)\n\n')
    if not rows:
        out.write("  (no results)\n")
        return
    out.write(
        "  #  ep_id                              subject                          stale  pending\n"
    )
    for i, row in enumerate(rows, 1):
        out.write(
            f"  {i:<2} {row.ep_id[:36]:<36} {_truncate(row.subject, 32):<32} "
            f"{'yes' if row.stale else 'no':<5}  "
            f"{'yes' if row.pending_ingest else 'no'}\n"
        )
    out.write("\n  Use `seahorse recall-timeline <ep_id>` for the chain.\n")
    out.write("  Use `seahorse recall-full <ep_id> ...` to hydrate body.\n")


def render_timeline(
    window: TimelineWindow, *, fmt: OutputFormat, out: TextIO
) -> None:
    """``recall-timeline`` output: ``TimelineWindow``."""
    if fmt == "json":
        out.write(to_json(window) + "\n")
        return
    if fmt == "jsonl":
        for entry in window.entries:
            out.write(to_json(entry) + "\n")
        return
    out.write(
        f"Timeline: anchor={window.anchor_ep_id} axis={window.axis} "
        f"({len(window.entries)} entries)\n\n"
    )
    for entry in window.entries:
        marker = "↳ " if entry.supersedes else "  "
        out.write(
            f"  {marker}{entry.ep_id[:36]:<36} {_truncate(entry.subject, 28):<28} "
            f"valid={_fmt_dt(entry.valid_at)} invalid={_fmt_dt(entry.invalid_at)}\n"
        )
    # Progressive disclosure (ADR-06): the middle rung hints at the next.
    out.write("\n  Use `seahorse recall-full <ep_id> ...` to hydrate body.\n")


def render_full_details(
    details: list[FullDetail], *, fmt: OutputFormat, out: TextIO
) -> None:
    """``recall-full`` output: ``list[FullDetail]`` (hydrated body)."""
    if fmt == "json":
        out.write(to_json(details) + "\n")
        return
    if fmt == "jsonl":
        for d in details:
            out.write(to_json(d) + "\n")
        return
    for d in details:
        ep = d.episode
        out.write(f"--- {ep.id} ---\n")
        out.write(f"  subject:    {_truncate(ep.subject or '-')}\n")
        out.write(f"  cognitive:  {ep.cognitive_type or '-'}\n")
        out.write(f"  valid_at:   {_fmt_dt(ep.valid_at)}\n")
        out.write(f"  invalid_at: {_fmt_dt(ep.invalid_at)}\n")
        out.write(f"  created_at: {_fmt_dt(ep.created_at)}\n")
        out.write(f"  stale={d.freshness.stale} pending={d.freshness.pending_ingest}\n")
        out.write("  body:\n")
        for line in ep.body.splitlines():
            out.write(f"    {line}\n")
        out.write("\n")


def render_message(
    payload: Any, *, fmt: OutputFormat, out: TextIO, human_text: str
) -> None:
    """Generic management output (status / init / index rebuild / uuid7).

    ``payload`` is the structured value for ``json``/``jsonl``; ``human_text``
    is the plain-text form. ``jsonl`` writes one line (payload is a single
    object, not a list).
    """
    if fmt in ("json", "jsonl"):
        out.write(to_json(payload) + "\n")
        return
    out.write(human_text)
    if not human_text.endswith("\n"):
        out.write("\n")


__all__ = [
    "OutputFormat",
    "to_jsonable",
    "to_json",
    "render_write_result",
    "render_episode",
    "render_index_rows",
    "render_timeline",
    "render_full_details",
    "render_message",
]