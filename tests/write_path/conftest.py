"""Shared fixtures + recording doubles for #5 write-path tests.

``RecordingEngine`` captures every ``remember`` call's kwargs so tests assert
WHAT was delegated, with WHICH provenance — not just the return value (the #8
adversarial-review lesson: outcome-only tests cannot catch provenance-shape
regressions). It also exposes a configurable ``is_valid_skip_path`` gate (the
#5 first-class skip-path border contract) so tests can drive the gate-valid,
gate-invalid, and gate-raising branches without a real engine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from seahorse.contracts.engine import WriteResult
from seahorse.contracts.episode import Episode
from seahorse.engine.errors import E_SKIP_CONTRACT_VIOLATED, EngineError


@dataclass
class RememberCall:
    body: str
    by: dict[str, Any]
    valid_at: datetime | None
    cognitive_type: str | None
    schema_version: str
    title: str | None
    now: datetime | None
    subject: str | None = None  # M4-C.3 LLM-path override (None on skip path)


@dataclass
class GateCall:
    """A recorded ``is_valid_skip_path`` invocation (the candidate episode)."""

    episode: Episode


class RecordingEngine:
    """Engine double that records ``remember`` + ``is_valid_skip_path`` calls.

    ``skip_gate`` controls the gate's return value when it does not raise;
    ``gate_raises`` makes it raise ``EngineError(E_SKIP_CONTRACT_VIOLATED)``
    (the gate-invalid branch that triggers the deterministic_extract fallback).
    """

    def __init__(
        self,
        result: WriteResult | None = None,
        *,
        skip_gate: bool = True,
        gate_raises: bool = False,
        gate_field: str = "created_at",
        gate_error_code: str | None = None,
    ) -> None:
        self.calls: list[RememberCall] = []
        self.gate_calls: list[GateCall] = []
        self._result = result or WriteResult(
            ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[]
        )
        self._skip_gate = skip_gate
        self._gate_raises = gate_raises
        self._gate_field = gate_field
        # When set, the gate raises EngineError(this code) instead of the default
        # E_SKIP_CONTRACT_VIOLATED — used to test run_skip_path's non-SKIP re-raise.
        self._gate_error_code = gate_error_code

    def remember(
        self,
        *,
        body: str,
        by: dict,
        valid_at: datetime | None = None,
        cognitive_type: str | None = None,
        schema_version: str = "1.1",
        title: str | None = None,
        subject: str | None = None,
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
                subject=subject,
            )
        )
        return self._result

    def is_valid_skip_path(self, ep: Episode) -> bool:
        self.gate_calls.append(GateCall(episode=ep))
        if self._gate_error_code is not None:
            raise EngineError(self._gate_error_code, field=self._gate_field)
        if self._gate_raises:
            raise EngineError(E_SKIP_CONTRACT_VIOLATED, field=self._gate_field)
        return self._skip_gate

    @property
    def remember_count(self) -> int:
        return len(self.calls)

    @property
    def gate_count(self) -> int:
        return len(self.gate_calls)

    @property
    def last_call(self) -> RememberCall:
        assert self.calls, "no remember call was recorded"
        return self.calls[-1]

    @property
    def last_gate_call(self) -> GateCall:
        assert self.gate_calls, "no gate call was recorded"
        return self.gate_calls[-1]


@pytest.fixture()
def engine() -> RecordingEngine:
    return RecordingEngine()


@pytest.fixture()
def seq() -> Sequence[RememberCall]:
    return []  # placeholder; tests use engine.calls directly


# silence unused-import warnings for fixtures provided for downstream files
__all__ = ["RecordingEngine", "RememberCall", "GateCall", "engine", "seq"]