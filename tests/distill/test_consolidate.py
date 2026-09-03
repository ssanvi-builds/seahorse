"""Tests for ``seahorse.distill.consolidate`` — the consolidate orchestration.

``consolidate(facade)`` reads the current-state set, clusters by subject
recurrence (N≥3), and distills each cluster into a consolidated semantic
episode via the facade. The consolidated body uses the stable clustering key as
its H1 (no ``[session_tag:n]`` suffix). The sources stay current-state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.distill.consolidate import consolidate
from seahorse.engine.errors import E_COLLISION_EXISTS, EngineError
from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload
from seahorse.llm import BudgetContext, ExtractResult

T0 = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


class _FakeLLMClient:
    """Recording double for the ``LLMClient`` Protocol (extract only)."""

    def __init__(self, result: ExtractResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def extract(
        self,
        content: str,
        schema_hint: type,
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        prompt_builder=None,
    ) -> ExtractResult:
        self.calls.append({"role": role, "content": content})
        return self.result


def _ok_result() -> ExtractResult:
    return ExtractResult(
        data={"consolidated_body": "# topic\n\nSynthesized knowledge."},
        prompt_hash="h" * 64,
        model_used="ollama/qwen3:1.7b",
        confidence=0.9,
    )


def _degraded_result() -> ExtractResult:
    return ExtractResult(data={}, prompt_hash="", degraded_to_skip=True)


def _remember(facade, *, body: str, now: datetime, source_type: str = "agent") -> None:
    facade.remember(
        RememberPayload(
            body=body,
            by={
                "source_type": source_type,
                "agent_id": "a1",
                "session_id": "sess-1",
            },
        ),
        now=now,
    )


def _facade(db_path):
    facade, storage = build_facade(db_path)
    return facade, storage


def test_consolidate_no_clusters(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        report = consolidate(facade)
        assert report.clusters_found == 0
        assert report.items == []
    finally:
        storage.close()


def test_consolidate_distills_recurrent_cluster(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Fix the flaky recall test [sess-1:{i + 1}]\n\nAttempt {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        report = consolidate(facade)
        assert report.clusters_found == 1
        assert report.items[0].key == "fix the flaky recall test"
        assert report.items[0].source_count == 3
        assert report.items[0].status == "ACTIVE"
        # The consolidated episode is a semantic knowledge note (4 current-state:
        # 3 sources + 1 consolidated).
        eps = facade.get_vigente()
        assert len(eps) == 4
        assert any(e.cognitive_type == "semantic" for e in eps)
    finally:
        storage.close()


def test_consolidate_sources_stay_vigente(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        consolidate(facade)
        eps = facade.get_vigente()
        # 4 current-state: 3 sources + 1 consolidated (none invalidated).
        assert len(eps) == 4
    finally:
        storage.close()


def test_consolidate_ignores_below_threshold(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(2):
            _remember(
                facade,
                body=f"# Rare topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        report = consolidate(facade)
        assert report.clusters_found == 0  # N<3 → no distillation
    finally:
        storage.close()


def test_consolidate_is_deterministic(tmp_path) -> None:
    """Two fresh facades with the same data produce the same report."""
    reports = []
    for idx in range(2):
        db_dir = tmp_path / f"db-{idx}"
        db_dir.mkdir(parents=True, exist_ok=True)
        facade, storage = _facade(db_dir / "seahorse.db")
        try:
            for i in range(3):
                _remember(
                    facade,
                    body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                    now=T0 + timedelta(minutes=i),
                )
            reports.append(consolidate(facade))
        finally:
            storage.close()
    assert [i.key for i in reports[0].items] == [i.key for i in reports[1].items]
    assert [i.source_count for i in reports[0].items] == [i.source_count for i in reports[1].items]


def test_consolidate_is_idempotent(tmp_path) -> None:
    """A cluster whose key already has a consolidated note is skipped —
    the second run does NOT create a duplicate knowledge note."""
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        first = consolidate(facade)
        assert first.clusters_found == 1
        second = consolidate(facade)
        assert second.clusters_found == 1  # the cluster still exists
        assert second.items == []  # but nothing new was distilled
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1  # no duplicate knowledge note
    finally:
        storage.close()


def test_consolidate_supersedes_when_new_episodes_arrive(tmp_path) -> None:
    # F7+ supersession: when a cluster whose key already has a consolidated note
    # gains NEW valid episodes, the note is UPDATED via improve (not duplicated).
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        first = consolidate(facade)
        assert first.clusters_found == 1
        # A new episode arrives (the representative changes).
        _remember(
            facade,
            body="# Topic [sess-1:4]\n\nDetail 4.",
            now=T0 + timedelta(minutes=3),
        )
        second = consolidate(facade, supersede=True)
        assert second.clusters_found == 1
        assert second.items[0].status == "ACTIVE"
        # The note was updated (superseded), not duplicated.
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
        assert consolidated[0].supersedes is not None
    finally:
        storage.close()


def test_consolidate_supersede_skips_when_no_new_episodes(tmp_path) -> None:
    # supersede=True with no new episodes → still idempotent (skip).
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        consolidate(facade)
        second = consolidate(facade, supersede=True)
        assert second.items == []
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
    finally:
        storage.close()


def test_consolidate_supersede_respects_human_edits(tmp_path) -> None:
    # Editorial authority: a human-edited note is NEVER superseded (the human
    # prevails — the distiller does not silently overwrite a human-authored fact).
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        consolidate(facade)
        _remember(
            facade,
            body="# Topic [sess-1:4]\n\nDetail 4.",
            now=T0 + timedelta(minutes=3),
        )
        # The human edited the note → skip (no supersession).
        second = consolidate(facade, supersede=True, human_edited=lambda ep: True)
        assert second.items == []
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
    finally:
        storage.close()


def test_consolidate_supersede_default_off_is_idempotent(tmp_path) -> None:
    # supersede defaults to False → the existing behavior (skip) is preserved.
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        consolidate(facade)
        _remember(
            facade,
            body="# Topic [sess-1:4]\n\nDetail 4.",
            now=T0 + timedelta(minutes=3),
        )
        second = consolidate(facade)  # supersede=False (default)
        assert second.items == []  # skipped, not superseded
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
    finally:
        storage.close()


def test_consolidate_synthesis_llm_uses_synthesized_body(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        client = _FakeLLMClient(_ok_result())
        report = consolidate(facade, synthesis="llm", llm_client=client)
        assert report.clusters_found == 1
        assert report.items[0].synthesis == "llm"
        # The consolidated episode carries the synthesized body + LLM provenance.
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
        ep = consolidated[0]
        assert "Synthesized knowledge." in (ep.body or "")
        assert ep.provenance["model_used"] == "ollama/qwen3:1.7b"
        assert ep.provenance["prompt_hash"] == "h" * 64
        assert ep.provenance["confidence"] == 0.9
        assert "degraded_from" not in ep.provenance
    finally:
        storage.close()


def test_consolidate_synthesis_degrade_falls_back_honestly(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        client = _FakeLLMClient(_degraded_result())
        report = consolidate(facade, synthesis="llm", llm_client=client)
        assert report.clusters_found == 1
        assert report.items[0].synthesis == "degraded"
        # The consolidated episode still exists (deterministic fallback) but
        # carries the honest degrade marker (C8.7).
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
        ep = consolidated[0]
        assert "Detail 3." in (ep.body or "")  # deterministic fallback body
        assert ep.provenance["degraded_from"] == "llm"
        assert ep.provenance["degrade_reason"] is not None
        assert ep.provenance["model_used"] is None
    finally:
        storage.close()


def test_consolidate_synthesis_llm_without_client_is_deterministic(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        # synthesis="llm" but no client → honest deterministic fallback (no LLM).
        report = consolidate(facade, synthesis="llm", llm_client=None)
        assert report.clusters_found == 1
        assert report.items[0].synthesis == "skip"
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
        assert "degraded_from" not in consolidated[0].provenance
    finally:
        storage.close()


def _stub_episode(ep_id: str, subject: str, *, now: datetime):
    """A minimal episode for the stub facade (cluster_episodes only needs
    subject / cognitive_type / provenance / supersedes / created_at)."""
    from seahorse.contracts.episode import Episode

    return Episode(
        id=ep_id,
        created_at=now,
        schema_version="1.1",
        provenance={"source_type": "agent", "extraction_mode": "skip"},
        body=f"# {subject}\n\nDetail for {ep_id}.",
        subject=subject,
        valid_at=now,
        cognitive_type="episodic",
        source_type="agent",
    )


class _CollidingFacade:
    """Stub facade whose distill raises the engine's collision error.

    Fault injection for the handled-collision path: a rival active episode
    holds the cluster key, so ``facade.distill`` raises
    ``EngineError(E_COLLISION_EXISTS)`` (the supersede path — engine.improve
    raises instead of returning a COLLISION WriteResult)."""

    def __init__(self, eps, *, failing_key: str, other_error_key: str = "") -> None:
        self._eps = eps
        self._failing_key = failing_key
        self._other_error_key = other_error_key
        self.distill_calls: list[str] = []

    def get_vigente(self):
        return list(self._eps)

    def distill(self, source_ep_ids, representative, consolidated_body, by,
                supersede_ep_id=None):
        self.distill_calls.append(representative.subject)
        if representative.subject == self._failing_key:
            raise EngineError(E_COLLISION_EXISTS, existing_id="rival-1")
        if representative.subject == self._other_error_key:
            raise EngineError("E_NOT_IN_MVP_0", primitive="distill")
        from seahorse.contracts.engine import WriteResult

        return WriteResult(ep_id="ep-new", fact_id="f" * 32, status="ACTIVE",
                           collisions_detected=[])


def test_consolidate_reports_collision_and_continues() -> None:
    """A cluster whose distill hits E_COLLISION_EXISTS is reported as a
    COLLISION row and the run continues with the remaining clusters — never a
    crash (loop L6b, 2026-09-02)."""
    eps = [
        _stub_episode(f"e{i}", "hot cluster", now=T0 + timedelta(minutes=i))
        for i in range(3)
    ] + [
        _stub_episode(f"f{i}", "calm cluster", now=T0 + timedelta(minutes=i))
        for i in range(3)
    ]
    facade = _CollidingFacade(eps, failing_key="hot cluster")
    report = consolidate(facade)
    assert report.clusters_found == 2
    by_key = {item.key: item for item in report.items}
    # The colliding cluster: handled, honest, no episode.
    assert by_key["hot cluster"].status == "COLLISION"
    assert by_key["hot cluster"].ep_id is None
    assert by_key["hot cluster"].source_count == 3
    # The healthy cluster was still distilled.
    assert by_key["calm cluster"].status == "ACTIVE"
    assert by_key["calm cluster"].ep_id == "ep-new"
    assert sorted(facade.distill_calls) == ["calm cluster", "hot cluster"]


def test_consolidate_reraises_non_collision_engine_error() -> None:
    """A non-collision EngineError from distill is NOT swallowed as a
    COLLISION row — it propagates fail-loud."""
    import pytest

    eps = [
        _stub_episode(f"e{i}", "broken cluster", now=T0 + timedelta(minutes=i))
        for i in range(3)
    ]
    facade = _CollidingFacade(
        eps, failing_key="never", other_error_key="broken cluster"
    )
    with pytest.raises(EngineError):
        consolidate(facade)


# --- absorb (design review post-v1.0, decision 1) -----------------------------
#
# A rival vigent episode holding the cluster key (e.g. an untagged remember on
# the same subject) used to collide FOREVER: the cluster regenerated a COLLISION
# row on every run and never distilled. The absorb policy invalidates a
# NON-HUMAN cluster-member rival (reason ``absorbed_by_consolidate`` — the
# audit trail preserves it, bi-temporally queryable at any PIT) and retries the
# distill once. A human-authored rival prevails: it may be a deliberate note,
# so the collision is reported with a resolution hint.


def test_consolidate_absorbs_untagged_agent_rival(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Deploy story [sess-1:{i + 1}]\n\nAttempt {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        # The untagged rival: same cluster key, holds the key's fact_id.
        _remember(
            facade,
            body="# deploy story\n\nA standalone untagged note.",
            now=T0 + timedelta(minutes=5),
        )
        report = consolidate(facade)
        assert report.clusters_found == 1
        item = report.items[0]
        assert item.status == "ACTIVE"
        assert item.ep_id is not None
        assert len(item.absorbed_rivals) == 1
        eps = facade.get_vigente()
        # The absorbed rival is no longer current-state...
        assert all(e.id not in item.absorbed_rivals for e in eps)
        # ...and exactly one consolidated note exists (no duplicate).
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1
        # The rival survives bi-temporally (soft-invalidated, never deleted):
        # the audit trail keeps the absorb traceable.
        row = facade.audit_log(item.absorbed_rivals[0])
        assert any(ev.primitive == "forget" for ev in row)
    finally:
        storage.close()


def test_consolidate_absorb_is_idempotent(tmp_path) -> None:
    """After an absorb, the next run skips the cluster (the note exists and
    the absorbed rival is no longer vigent) — no repeated absorb, no noise."""
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Deploy story [sess-1:{i + 1}]\n\nAttempt {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        _remember(
            facade,
            body="# deploy story\n\nA standalone untagged note.",
            now=T0 + timedelta(minutes=5),
        )
        first = consolidate(facade)
        assert first.items[0].status == "ACTIVE"
        second = consolidate(facade)
        assert second.items == []  # idempotent skip — the note exists
    finally:
        storage.close()


def test_consolidate_human_rival_keeps_collision_with_hint(tmp_path) -> None:
    """A human-authored untagged rival is NEVER absorbed (editorial authority):
    the collision is reported honestly, with the resolution hint."""
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Deploy story [sess-1:{i + 1}]\n\nAttempt {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        _remember(
            facade,
            body="# deploy story\n\nA human-authored standalone note.",
            now=T0 + timedelta(minutes=5),
            source_type="human",
        )
        report = consolidate(facade)
        item = report.items[0]
        assert item.status == "COLLISION"
        assert item.ep_id is None
        assert item.absorbed_rivals == ()
        assert "seahorse forget" in item.detail
        # The human rival is untouched.
        eps = facade.get_vigente()
        assert len(eps) == 4
    finally:
        storage.close()


class _RetryCollidingFacade:
    """Stub facade: distill returns a COLLISION WriteResult whose rival is an
    absorbable cluster member, forget works, but the RETRY still collides
    (another rival appeared). Pins the honest report: absorbed rivals are
    surfaced even when the note still fails."""

    def __init__(self, eps) -> None:
        self._eps = eps
        self.forgotten: list[str] = []
        self.distill_calls = 0

    def get_vigente(self):
        return list(self._eps)

    def distill(self, source_ep_ids, representative, consolidated_body, by,
                supersede_ep_id=None):
        from seahorse.contracts.engine import WriteResult
        from seahorse.engine.collision import Collision

        self.distill_calls += 1
        return WriteResult(
            ep_id=None,
            fact_id=None,
            status="COLLISION",
            collisions_detected=[Collision(kind="concurrent", existing_id="rival-1",
                                           fact_id="f" * 32)],
        )

    def forget(self, ep_id, *, reason, by, now=None):
        self.forgotten.append((ep_id, reason))


def test_consolidate_retry_collision_still_reports_absorbed_rivals() -> None:
    eps = [
        _stub_episode(f"e{i}", "hot cluster", now=T0 + timedelta(minutes=i))
        for i in range(3)
    ]
    # Make e0 the rival the collision names (a cluster member, agent source).
    eps[0] = eps[0].model_copy(update={"id": "rival-1"})
    facade = _RetryCollidingFacade(eps)
    report = consolidate(facade)
    item = report.items[0]
    assert item.status == "COLLISION"
    assert item.absorbed_rivals == ("rival-1",)
    assert facade.forgotten == [("rival-1", "absorbed_by_consolidate")]
    assert facade.distill_calls == 2  # initial + one retry, no loop
