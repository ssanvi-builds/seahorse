"""Python → wire JSON serializer for the MCP profile (#13).

Owns the datetime→ISO-8601 policy for the wire (R9 f5-13): timezone-aware
datetimes serialize to UTC with a ``Z`` suffix (F3.1 §4), preserving
microseconds. ``exclude_none=False`` — nulls are explicit so the wire shape is
stable across MVP-0 → MVP-1 (a field that is null now stays null, not absent).

All payload types are ``@dataclass(frozen=True)`` (some with nested dataclasses:
``FullDetail`` carries ``Episode`` + ``EpisodeProvenance`` + ``FreshnessView``;
``TimelineWindow`` carries ``tuple[TimelineEntry, ...]``). This walker recurses
through dataclasses and converts datetimes inside, without using
``dataclasses.asdict`` (which leaves ``datetime`` as ``datetime``).

``SeahorseError`` uses ``__slots__`` (not a dataclass) → serialized manually as
``{"code", "detail"}``.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from seahorse.facade.errors import SeahorseError


def _iso_z(dt: datetime) -> str:
    """Canonicalize a datetime to UTC ISO-8601 with a ``Z`` suffix.

    Preserves microseconds. ``+00:00`` → ``Z`` (F3.1 §4). Naive datetimes are
    assumed UTC (the facade/engine always produce timezone-aware UTC; this is
    a defensive canonicalization, not a silent timezone shift).
    """
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def to_wire(obj: Any) -> Any:
    """Recursively convert a Python object to a JSON-able wire value.

    - ``datetime`` → ISO-8601 UTC string with ``Z``
    - ``UUID`` → ``str``
    - dataclass instance → ``{field.name: to_wire(value)}`` (None kept as null)
    - ``tuple``/``list``/``set``/``frozenset`` → JSON array
    - ``dict`` → ``{k: to_wire(v)}``
    - ``str``/``int``/``float``/``bool``/``None`` → as-is
    - ``SeahorseError`` → ``{"code", "detail"}``
    """
    if obj is None:
        return None
    if isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, datetime):
        return _iso_z(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, SeahorseError):
        return {"code": obj.code, "detail": obj.detail}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_wire(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (tuple, list, set, frozenset)):
        return [to_wire(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_wire(v) for k, v in obj.items()}
    # Fallback: let json.dumps handle it (or fail loud).
    return obj


def to_json(obj: Any) -> str:
    """Serialize to a compact JSON string (wire representation)."""
    return json.dumps(to_wire(obj), ensure_ascii=False, sort_keys=False)


def to_text_content(obj: Any) -> dict[str, Any]:
    """Wrap a result as an MCP ``tools/call`` text content block."""
    return {"type": "text", "text": to_json(obj)}


def success_response(request_id: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC success response for ``tools/call``."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [to_text_content(result)],
            "isError": False,
        },
    }


__all__ = ["to_wire", "to_json", "to_text_content", "success_response", "_iso_z"]