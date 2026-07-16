"""Tests for the passthrough methods — ``freshness_view`` / ``audit_log`` /
``follow_supersedes_chain``. Each delegates to the engine exactly once with no
transformation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.engine import AuditEvent, Episode, FreshnessView
from tests.facade.conftest import make_episode


class TestFreshnessView:
    def test_delegates_once(self, facade, engine) -> None:
        engine.freshness_result = FreshnessView(
            fact_id="f1", age_days=3, stale=False, pending_ingest=False, regime="agent"
        )
        result = facade.freshness_view("e1")
        assert len(engine.freshness_calls) == 1
        assert engine.freshness_calls[0]["ep_id"] == "e1"
        assert result.age_days == 3

    def test_uses_clock_for_now(self, facade, engine) -> None:
        facade.freshness_view("e1")
        # clock fixture returns a fixed datetime; now must be that value.
        assert engine.freshness_calls[0]["now"] == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class TestAuditLog:
    def test_delegates_once(self, facade, engine) -> None:
        engine.audit_result = [
            AuditEvent(
                primitive="apply",
                target_id="e1",
                transaction_time=datetime(2026, 1, 1, tzinfo=UTC),
                result="added",
            )
        ]
        result = facade.audit_log("e1")
        assert len(engine.audit_calls) == 1
        assert engine.audit_calls[0]["ep_id"] == "e1"
        assert len(result) == 1
        assert result[0].primitive == "apply"

    def test_returns_verbatim_list(self, facade, engine) -> None:
        engine.audit_result = []
        assert facade.audit_log("e1") == []


class TestFollowSupersedesChain:
    def test_delegates_once(self, facade, engine) -> None:
        engine.chain_result = [make_episode("e1"), make_episode("e2", supersedes="e1")]
        result = facade.follow_supersedes_chain("e1")
        assert len(engine.chain_calls) == 1
        assert engine.chain_calls[0]["ep_id"] == "e1"
        assert [e.id for e in result] == ["e1", "e2"]

    def test_returns_verbatim(self, facade, engine) -> None:
        engine.chain_result: list[Episode] = []
        assert facade.follow_supersedes_chain("e1") == []