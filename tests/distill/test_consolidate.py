"""Tests for ``seahorse.distill.consolidate`` — the consolidate orchestration.

``consolidate(facade)`` reads the current-state set, clusters by subject
recurrence (N≥3), and distills each cluster into a consolidated semantic
episode via the facade. The consolidated body uses the stable clustering key as
its H1 (no ``[session_tag:n]`` suffix). The sources stay current-state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.distill.consolidate import consolidate
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


def _remember(facade, *, body: str, now: datetime) -> None:
    facade.remember(
        RememberPayload(
            body=body,
            by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
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
