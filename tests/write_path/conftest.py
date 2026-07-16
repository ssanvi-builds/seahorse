"""Shared fixtures + recording doubles for #5 write-path tests.

``RecordingEngine`` captures every ``remember`` call's kwargs so tests assert
WHAT was delegated, with WHICH provenance — not just the return value (the #8
adversarial-review lesson: outcome-only tests cannot catch provenance-shape
regressions).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from seahorse.contracts.engine import WriteResult


@dataclass
class RememberCall:
    body: str
    by: dict[str, Any]
    valid_at: datetime | None
    cognitive_type: str | None
    schema_version: str
    title: str | None
    now: datetime | None


class RecordingEngine:
    """Engine double that records ``remember`` calls and returns a fixed result."""

    def __init__(self, result: WriteResult | None = None) -> None:
        self.calls: list[RememberCall] = []
        self._result = result or WriteResult(
            ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[]
        )

    def remember(
        self,
        *,
        body: str,
        by: dict,
        valid_at: datetime | None = None,
        cognitive_type: str | None = None,
        schema_version: str = "1.1",
        title: str | None = None,
        now: datetime | None = None,
    ) -> WriteResult:
        self.calls.append(
            RememberCall(
                body=body,
                by=dict(by),
                valid_at=valid_at,
                cognitive_type=cognitive_type,
                schema_version=schema_version,
                title=title,
                now=now,
            )
        )
        return self._result

    @property
    def remember_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> RememberCall:
        assert self.calls, "no remember call was recorded"
        return self.calls[-1]


@pytest.fixture()
def engine() -> RecordingEngine:
    return RecordingEngine()


@pytest.fixture()
def seq() -> Sequence[RememberCall]:
    return []  # placeholder; tests use engine.calls directly


# silence unused-import warnings for fixtures provided for downstream files
__all__ = ["RecordingEngine", "RememberCall", "engine", "seq"]