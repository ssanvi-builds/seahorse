"""Tests for the later-release stubs — ``expire`` / ``revalidate``.

Both primitives are a medium-term goal / a later release and are refused at the
facade border with ``E_NOT_IN_MVP_0_1`` BEFORE the engine is touched. This keeps
the contract honest (fail-loud honesty): no silent degradation, no
half-implemented primitive.

The ``mvp1_axis`` marker keeps these later-release stubs visible to the runner
without gating the first-release green suite — they flip en masse when the
expire/revalidate primitives materialize.
"""

from __future__ import annotations

import pytest

from seahorse.facade.errors import E_NOT_IN_MVP_0_1, SeahorseError

pytestmark = pytest.mark.mvp1_axis


class TestExpireStub:
    def test_raises_not_in_mvp(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.expire("e1")
        assert exc.value.code == E_NOT_IN_MVP_0_1

    def test_does_not_touch_engine(self, facade, engine, write_path) -> None:
        with pytest.raises(SeahorseError):
            facade.expire("e1")
        # No engine mutator/reader was called; no write-path ingest.
        assert engine.improve_calls == []
        assert engine.forget_calls == []
        assert engine.audit_calls == []
        assert engine.freshness_calls == []
        assert write_path.ingest_calls == []

    def test_expire_no_read_calls(self, facade, engine) -> None:
        with pytest.raises(SeahorseError):
            facade.expire("e1")
        # Read-side engine methods must also be untouched on the error path.
        assert engine.get_vigente_calls == []
        assert engine.chain_calls == []


class TestRevalidateStub:
    def test_raises_not_in_mvp(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.revalidate("e1", by={"source_type": "agent"})
        assert exc.value.code == E_NOT_IN_MVP_0_1

    def test_does_not_touch_engine_or_write_path(
        self, facade, engine, write_path
    ) -> None:
        with pytest.raises(SeahorseError):
            facade.revalidate("e1", by={"source_type": "agent"})
        assert engine.improve_calls == []
        assert engine.forget_calls == []
        assert write_path.ingest_calls == []

    def test_revalidate_no_engine_calls(self, facade, engine) -> None:
        with pytest.raises(SeahorseError):
            facade.revalidate("e1", by={"source_type": "agent"})
        # Full engine call-set must be empty on the error path.
        assert engine.improve_calls == []
        assert engine.forget_calls == []
        assert engine.audit_calls == []
        assert engine.freshness_calls == []
        assert engine.get_vigente_calls == []
        assert engine.chain_calls == []