"""Wire JSON → Python payload codec for the MCP profile (#13).

Pure translation: wire JSON (already wire-shape-validated by ``validate.py``)
becomes Python primitives the facade accepts. This module holds NO domain
logic and does NOT call the facade — it parses datetimes, builds the
``Provenance`` plain dict, constructs the frozen ``RememberPayload``, and
extracts the raw PIT inputs (``pit`` object, ``pit_kind``, ``t``) into Python
values. The precedence + kind validation of PIT is delegated to
``facade.build_pit`` in the tool handlers (commit 3) — #13 never replicates
PIT validation (delegation purity, R2 f5-13).

Why no facade here: ``deserialize.py`` is a pure codec (commit 2 = "pure
codecs, no facade calls"). Putting ``facade.build_pit`` here would couple the
codec to the facade and force every caller to thread a facade through. The
handlers own the one ``facade.build_pit`` call that resolves PIT.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from seahorse.disclosure.types import PITPoint
from seahorse.facade.types import Provenance, RememberPayload


def parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 wire string (with ``Z``) into a timezone-aware datetime.

    ``datetime.fromisoformat`` (3.11+) accepts ``Z`` directly, but we normalize
    to ``+00:00`` for older semantics and to keep the contract explicit. The
    result is timezone-aware UTC (wire-shape already enforced a timezone).
    """
    if not isinstance(value, str):
        raise TypeError(f"parse_dt expects str, got {type(value).__name__}")
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        # Defensive: wire-shape enforces timezone, but a naive slip is treated
        # as UTC rather than silently shifting (matches serialize._iso_z).

        dt = dt.replace(tzinfo=UTC)
    return dt


def parse_pit_point(obj: dict[str, Any]) -> PITPoint:
    """Build a ``PITPoint`` from a wire ``{"kind", "t"}`` object.

    Wire-shape already validated ``kind`` ∈ {state_at, known_at} and ``t`` is a
    date-time. This only parses ``t`` and constructs the frozen dataclass. The
    kind is NOT re-validated here — ``facade.build_pit`` owns that (so a future
    kind set change lives in one place).
    """
    return PITPoint(kind=cast(Any, obj["kind"]), t=parse_dt(obj["t"]))


def extract_pit(
    args: dict[str, Any], *, t_field: str
) -> tuple[PITPoint | None, str | None, datetime | None]:
    """Extract the three PIT inputs from a tool's arguments.

    Returns ``(pit, pit_kind, t)`` where:
    - ``pit`` is a parsed ``PITPoint`` if ``args["pit"]`` is an object, else ``None``.
    - ``pit_kind`` is the raw string (or ``None``) — NOT validated here.
    - ``t`` is a parsed datetime if ``args[t_field]`` is present, else ``None``.

    ``t_field`` is ``"pit_t"`` for recall/recall_timeline/recall_full and ``"t"``
    for build_pit (the two wire shapes differ on the loose-t key name).

    Precedence (``pit`` wins over ``pit_kind``+``t``) is resolved by
    ``facade.build_pit`` in the handler, NOT here.
    """
    pit_obj = args.get("pit")
    pit = parse_pit_point(pit_obj) if isinstance(pit_obj, dict) else None
    pit_kind = args.get("pit_kind")
    t_raw = args.get(t_field)
    t = parse_dt(t_raw) if isinstance(t_raw, str) else None
    return pit, pit_kind, t


def build_provenance(by_obj: dict[str, Any]) -> Provenance:
    """Build a ``Provenance`` (plain dict) from the wire ``by`` object.

    ``Provenance`` is a ``TypedDict(total=False)`` — a plain dict at runtime.
    Wire-shape already enforced ``required`` + ``additionalProperties: false``,
    so we shallow-copy (immutability: never return the caller's dict) and pass
    through verbatim. No datetime fields exist in ``Provenance``.
    """
    return cast(Provenance, dict(by_obj))


def build_remember_payload(args: dict[str, Any]) -> RememberPayload:
    """Build a frozen ``RememberPayload`` from ``remember`` tool arguments.

    ``title`` is NOT accepted on the wire (the engine derives it from the body's
    H1). ``summary`` is an additive editorial field (OQ3 enabler): when absent,
    the write path derives a deterministic fallback (first sentence of the body).
    ``schema_version`` is pinned to ``"1.1"`` (F3.1). ``tags`` is forwarded as a
    tuple; the facade rejects a non-empty ``tags`` with ``E_NOT_IN_MVP_0_1``
    (MVP-0 honesty) — #13 does not pre-reject, it delegates.
    """
    valid_at_raw = args.get("valid_at")
    return RememberPayload(
        body=args["body"],
        by=build_provenance(args["by"]),
        valid_at=parse_dt(valid_at_raw) if isinstance(valid_at_raw, str) else None,
        cognitive_type=args.get("cognitive_type"),
        summary=args.get("summary"),
        tags=tuple(args.get("tags") or ()),
        schema_version="1.1",
    )


__all__ = [
    "parse_dt",
    "parse_pit_point",
    "extract_pit",
    "build_provenance",
    "build_remember_payload",
]