"""``BaseAdapter`` — shared utilities for dataset adapters (f5-16 §4)."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_date(value) -> datetime | None:
    """Parse a dataset date field to an aware UTC datetime (or None).

    Accepts ``datetime`` instances, ISO-8601 strings (with or without ``Z``),
    and epoch numbers. Naive values are assumed UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = ["parse_date"]
