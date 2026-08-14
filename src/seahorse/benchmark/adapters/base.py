"""``BaseAdapter`` — shared utilities for dataset adapters."""

from __future__ import annotations

import re
from datetime import UTC, datetime

# LongMemEval dates are ``YYYY/MM/DD (Weekday) HH:MM``, where the
# English weekday is redundant with the calendar date. ``%a`` is locale-
# dependent, so the weekday token is stripped before parsing — the date fields
# are deterministic from the numeric part alone.
_LMEB_DATE_RE = re.compile(r"^(\d{4}/\d{2}/\d{2})\s*\([^)]*\)\s+(\d{2}:\d{2})$")


def _parse_lmeb_date(value: str) -> datetime | None:
    """Parse ``2023/05/30 (Tue) 23:40`` → aware UTC datetime (or None)."""
    m = _LMEB_DATE_RE.match(value)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y/%m/%d %H:%M").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def parse_date(value) -> datetime | None:
    """Parse a dataset date field to an aware UTC datetime (or None).

    Accepts ``datetime`` instances, ISO-8601 strings (with or without ``Z``),
    epoch numbers, and the LongMemEval ``YYYY/MM/DD (Weekday) HH:MM`` format.
    Naive values are assumed UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        lmeb = _parse_lmeb_date(value)
        if lmeb is not None:
            return lmeb
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = ["parse_date"]
