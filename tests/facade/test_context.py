"""Tests for ``MemoryFacade.context()`` — the shared bootstrap method.

The context bootstrap is by RECENCY, not semantics (claude-mem does not inject
semantic context — it injects recency + summaries + a fetch pointer; Seahorse
replicates this behavior). Five blocks at INDEX level, no body:
(1) recent episodes (created_at desc, ep_id asc — the listing-regime sort,
deterministic); (2) the current-state listing; (3) last session grouped by
provenance.session_id (INDEX list, NOT an abstractive summary — honesty);
(4) knowledge notes — the most recent consolidated / project_doc episodes
(the distilled surface); (5) header + counter + pointer. Deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload

T0 = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def _remember(facade, *, body: str, session_id: str, now: datetime) -> None:
    # The body's H1 becomes the subject (title > H1 > None).
    facade.remember(
        RememberPayload(
            body=f"# {body}\n\nDetails about {body}.",
            by={"source_type": "agent", "agent_id": "agent-1", "session_id": session_id},
        ),
        now=now,
    )


def _ctx(facade) -> dict:
    return facade.context()


# ---------------------------------------------------------------------------
# empty DB
# ---------------------------------------------------------------------------


def test_context_empty_db(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        data = _ctx(facade)
        assert data.recent == []
        assert data.vigente_count == 0
        assert data.last_session_id is None
        assert data.last_session == []
        assert data.total_episodes == 0
    finally:
        storage.close()


# ---------------------------------------------------------------------------
# recent episodes — deterministic listing-regime sort
# ---------------------------------------------------------------------------


def test_context_recent_sorted_by_created_at_desc(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        _remember(facade, body="First fact about alpha", session_id="sess-1", now=T0)
        _remember(
            facade, body="Second fact about beta", session_id="sess-1", now=T0 + timedelta(hours=1)
        )
        _remember(
            facade, body="Third fact about gamma", session_id="sess-1", now=T0 + timedelta(hours=2)
        )
        data = _ctx(facade)
        assert [e.subject for e in data.recent] == [
            "third fact about gamma",
            "second fact about beta",
            "first fact about alpha",
        ]
        assert data.total_episodes == 3
        assert data.vigente_count == 3
    finally:
        storage.close()


def test_context_recent_capped_at_top_k(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        for i in range(15):
            _remember(
                facade, body=f"Fact number {i}", session_id="sess-1", now=T0 + timedelta(minutes=i)
            )
        data = _ctx(facade)
        assert len(data.recent) == 10  # TOP_K default
    finally:
        storage.close()


# ---------------------------------------------------------------------------
# last session — grouped by provenance.session_id
# ---------------------------------------------------------------------------


def test_context_last_session_is_most_recent(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        _remember(facade, body="Old session fact", session_id="sess-old", now=T0)
        _remember(
            facade, body="New session fact one", session_id="sess-new", now=T0 + timedelta(hours=1)
        )
        _remember(
            facade, body="New session fact two", session_id="sess-new", now=T0 + timedelta(hours=2)
        )
        data = _ctx(facade)
        assert data.last_session_id == "sess-new"
        assert {e.subject for e in data.last_session} == {
            "new session fact one",
            "new session fact two",
        }
    finally:
        storage.close()


def test_context_last_session_is_index_list_not_summary(tmp_path) -> None:
    """Honesty: 'last session' is an INDEX list, not an abstractive summary —
    Seahorse has no session summaries yet. Declared, not faked."""
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        _remember(facade, body="Session fact", session_id="sess-1", now=T0)
        data = _ctx(facade)
        assert data.last_session_id == "sess-1"
        assert len(data.last_session) == 1
        # Each entry is an INDEX row (subject + summary), not a session blob.
        assert data.last_session[0].subject == "session fact"
    finally:
        storage.close()


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_context_is_deterministic(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        _remember(facade, body="Fact alpha", session_id="sess-1", now=T0)
        _remember(
            facade, body="Fact beta", session_id="sess-1", now=T0 + timedelta(minutes=1)
        )
        a = _ctx(facade)
        b = _ctx(facade)
        assert [e.ep_id for e in a.recent] == [e.ep_id for e in b.recent]
        assert a.last_session_id == b.last_session_id
    finally:
        storage.close()


# ---------------------------------------------------------------------------
# knowledge notes — the distilled surface (consolidated / project_doc)
# ---------------------------------------------------------------------------


def _remember_doc(facade, *, subject: str, session_id: str, now: datetime) -> None:
    facade.remember(
        RememberPayload(
            body=f"# {subject}\n\nContext / Options / Decision.",
            by={"source_type": "agent", "agent_id": "agent-1", "session_id": session_id},
            cognitive_type="project_doc",
        ),
        now=now,
    )


def test_context_empty_db_has_no_knowledge_notes(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        data = _ctx(facade)
        assert data.knowledge == []
    finally:
        storage.close()


def test_context_surfaces_project_doc_notes_only(tmp_path) -> None:
    """A project_doc note lands in ``knowledge``; plain episodes never do."""
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        _remember(facade, body="Plain episodic fact", session_id="sess-1", now=T0)
        _remember_doc(
            facade, subject="Use SQLite WAL", session_id="sess-1", now=T0 + timedelta(hours=1)
        )
        data = _ctx(facade)
        assert [e.subject for e in data.knowledge] == ["use sqlite wal"]
        assert data.knowledge[0].cognitive_type == "project_doc"
        # The plain episode stays OUT of knowledge but its recent row carries
        # the doc's type once the doc is recent too (the field is additive,
        # present on every ContextEpisode row).
        doc_row = next(e for e in data.recent if e.subject == "use sqlite wal")
        assert doc_row.cognitive_type == "project_doc"
    finally:
        storage.close()


def test_context_surfaces_consolidated_notes(tmp_path) -> None:
    """A real consolidated note (via `consolidate`) is bootstrap-visible."""
    from seahorse.distill.consolidate import consolidate

    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"Topic [sess-1:{i + 1}]",
                session_id="sess-1",
                now=T0 + timedelta(minutes=i),
            )
        report = consolidate(facade)
        assert report.items and report.items[0].status == "ACTIVE"
        data = _ctx(facade)
        assert [e.subject for e in data.knowledge] == ["topic"]
        assert data.knowledge[0].cognitive_type == "semantic"
    finally:
        storage.close()


def test_context_knowledge_most_recent_first_and_capped_at_five(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        for i in range(7):
            _remember_doc(
                facade,
                subject=f"Doc {i}",
                session_id="sess-1",
                now=T0 + timedelta(minutes=i),
            )
        data = _ctx(facade)
        assert len(data.knowledge) == 5
        assert [e.subject for e in data.knowledge] == [
            "doc 6",
            "doc 5",
            "doc 4",
            "doc 3",
            "doc 2",
        ]
    finally:
        storage.close()


def test_context_knowledge_is_deterministic(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        _remember_doc(facade, subject="Doc one", session_id="sess-1", now=T0)
        _remember_doc(
            facade, subject="Doc two", session_id="sess-1", now=T0 + timedelta(minutes=1)
        )
        a = _ctx(facade)
        b = _ctx(facade)
        assert [e.ep_id for e in a.knowledge] == [e.ep_id for e in b.knowledge]
    finally:
        storage.close()
