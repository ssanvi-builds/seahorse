"""Facade materialization hooks — one injection point covers all four write
surfaces (M6).

``remember`` (ACTIVE), ``distill``, and ``improve`` (successor) materialize the
new episode; ``improve`` (old) and ``forget`` invalidate the .md (C1). The hook
is best-effort (M9): a materializer failure never propagates to the caller, and
the guard fires BEFORE any engine read when no materializer is wired.
"""

from __future__ import annotations

from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import RememberPayload
from tests.facade.conftest import (
    RecordingMaterializer,
    make_episode,
    make_facade,
)


def _payload(**kw) -> RememberPayload:
    base = {"source_type": "agent"}
    base.update(kw.pop("by_extra", {}))
    return RememberPayload(body=kw.pop("body", "hello world"), by=base, **kw)


def _mat() -> RecordingMaterializer:
    return RecordingMaterializer()


# ---------------------------------------------------------------------------
# remember — ACTIVE writes materialize.
# ---------------------------------------------------------------------------


class TestRememberMaterializes:
    def test_active_write_materializes_fetched_episode(self, engine) -> None:
        ep = make_episode("e1")
        engine.get_result = ep
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f._write_path.result = WriteResult(
            ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[]
        )
        f.remember(_payload())
        assert engine.get_calls == [{"ep_id": "e1"}]
        assert mat.materialize_calls == [ep]  # the fetched episode, not the id

    def test_non_active_write_does_not_materialize(self, engine) -> None:
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f._write_path.result = WriteResult(
            ep_id=None, fact_id=None, status="NOOP", collisions_detected=[]
        )
        f.remember(_payload())
        assert engine.get_calls == []
        assert mat.materialize_calls == []

    def test_no_materializer_guard_fires_before_read(self, engine) -> None:
        f, _log = make_facade(engine=engine)  # materializer=None
        f._write_path.result = WriteResult(
            ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[]
        )
        f.remember(_payload())
        assert engine.get_calls == []  # the guard fires before any engine read

    def test_missing_episode_skips_materialize(self, engine) -> None:
        engine.get_result = None  # engine.get → None (race / deleted)
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f._write_path.result = WriteResult(
            ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[]
        )
        f.remember(_payload())
        assert mat.materialize_calls == []

    def test_materializer_failure_is_best_effort(self, engine) -> None:
        """M9 — a materializer exception never fails the write path."""
        engine.get_result = make_episode("e1")
        mat = _mat()
        mat.raise_on = "materialize"
        f, _log = make_facade(engine=engine, materializer=mat)
        f._write_path.result = WriteResult(
            ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[]
        )
        result = f.remember(_payload())  # must NOT raise
        assert result.status == "ACTIVE"


# ---------------------------------------------------------------------------
# improve — successor materialized, old .md invalidated (C1).
# ---------------------------------------------------------------------------


class TestImproveMaterializes:
    def test_successor_materialized_and_old_invalidated(self, engine) -> None:
        new_ep = make_episode("e2", supersedes="e1")
        old_ep = make_episode("e1")
        engine.improve_result = new_ep
        engine.get_by_id = {"e2": new_ep, "e1": old_ep}
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f.improve("e1", "new body", by={"source_type": "human"})
        assert mat.materialize_calls == [new_ep]
        assert mat.invalidate_calls == [old_ep]

    def test_old_missing_skips_invalidate(self, engine) -> None:
        new_ep = make_episode("e2")
        engine.improve_result = new_ep
        engine.get_by_id = {"e2": new_ep, "e1": None}  # old episode gone
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f.improve("e1", "new body", by={"source_type": "human"})
        assert mat.materialize_calls == [new_ep]
        assert mat.invalidate_calls == []

    def test_invalidate_failure_is_best_effort(self, engine) -> None:
        new_ep = make_episode("e2")
        engine.improve_result = new_ep
        engine.get_by_id = {"e2": new_ep, "e1": make_episode("e1")}
        mat = _mat()
        mat.raise_on = "invalidate"
        f, _log = make_facade(engine=engine, materializer=mat)
        result = f.improve("e1", "new body", by={"source_type": "human"})  # must NOT raise
        assert result.id == "e2"


# ---------------------------------------------------------------------------
# forget — the invalidated episode is merged into the .md (C1).
# ---------------------------------------------------------------------------


class TestForgetInvalidates:
    def test_forget_invalidates_materialized_note(self, engine) -> None:
        invalidated = make_episode("e1", invalid_at=None)
        engine.forget_result = invalidated
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f.forget("e1", reason="stale", by={"source_type": "human"})
        # ``result`` IS the invalidated episode — no re-fetch needed.
        assert mat.invalidate_calls == [invalidated]
        assert mat.materialize_calls == []

    def test_forget_invalidate_failure_is_best_effort(self, engine) -> None:
        engine.forget_result = make_episode("e1")
        mat = _mat()
        mat.raise_on = "invalidate"
        f, _log = make_facade(engine=engine, materializer=mat)
        result = f.forget("e1", reason="stale", by={"source_type": "human"})  # must NOT raise
        assert result.id == "e1"


# ---------------------------------------------------------------------------
# distill — the consolidated note is materialized (the default mode's purpose).
# ---------------------------------------------------------------------------


class TestDistillMaterializes:
    def test_distill_materializes_consolidated_note(self, engine) -> None:
        rep = make_episode("e3")
        engine.get_result = make_episode("e9")  # the WriteResult ep_id
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f.distill(
            source_ep_ids=["e1", "e2", "e3"],
            representative=rep,
            consolidated_body="# The capital of France\n\nParis.",
            by={"source_type": "system", "agent_id": "consolidator"},
        )
        assert engine.remember_calls  # the distill primitive wrote via engine.remember
        assert mat.materialize_calls == [make_episode("e9")]  # the WriteResult ep_id

    def test_distill_materializer_failure_is_best_effort(self, engine) -> None:
        rep = make_episode("e3")
        mat = _mat()
        mat.raise_on = "materialize"
        f, _log = make_facade(engine=engine, materializer=mat)
        result = f.distill(
            source_ep_ids=["e1", "e2", "e3"],
            representative=rep,
            consolidated_body="# The capital of France\n\nParis.",
            by={"source_type": "system", "agent_id": "consolidator"},
        )  # must NOT raise
        assert result.status == "ACTIVE"

    def test_distill_supersede_invalidates_old_note(self, engine) -> None:
        """F7+ supersession: the old note is invalidated (C1).

        ``distill_episodes`` calls ``engine.improve`` directly (bypassing
        ``facade.improve``), so the invalidation must happen in the distill
        hook — the old episode now carries ``invalid_at`` in the DB.
        """
        new_ep = make_episode("e2")
        old_ep = make_episode("e1")
        engine.improve_result = new_ep
        engine.get_by_id = {"e2": new_ep, "e1": old_ep}
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f.distill(
            source_ep_ids=["e1", "e2", "e3"],
            representative=make_episode("e3"),
            consolidated_body="# The capital of France\n\nParis.",
            by={"source_type": "system", "agent_id": "consolidator"},
            supersede_ep_id="e1",
        )
        assert mat.materialize_calls == [new_ep]
        assert mat.invalidate_calls == [old_ep]

    def test_distill_supersede_old_missing_skips_invalidate(self, engine) -> None:
        new_ep = make_episode("e2")
        engine.improve_result = new_ep
        engine.get_by_id = {"e2": new_ep, "e1": None}  # old note gone
        mat = _mat()
        f, _log = make_facade(engine=engine, materializer=mat)
        f.distill(
            source_ep_ids=["e1", "e2", "e3"],
            representative=make_episode("e3"),
            consolidated_body="# The capital of France\n\nParis.",
            by={"source_type": "system", "agent_id": "consolidator"},
            supersede_ep_id="e1",
        )
        assert mat.materialize_calls == [new_ep]
        assert mat.invalidate_calls == []
