"""SqliteSidecarIndexRepository tests (Phase 5b). episode_paths upsert + reindex ctx."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from seahorse.contracts.episode import Episode
from seahorse.contracts.persistence import ParsedNote, SidecarIndexRepository
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository


@pytest.fixture()
def sidecar(tmp_path) -> SqliteSidecarIndexRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4)
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteSidecarIndexRepository(mgr)
    yield repo
    mgr.close()


def _episode(
    ep_id: str = "e1",
    *,
    fact_id: str | None = "f1",
    subject: str | None = "Sergio",
    invalid_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
    supersedes_reason: str | None = None,
    extraction_mode: str = "skip",
    title: str | None = "Title",
    summary: str | None = "Summary",
    cognitive_type: str | None = "fact",
    source_type: str | None = "agent",
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"agent": "test", "extraction_mode": extraction_mode},
        body="body",
        subject=subject,
        fact_id=fact_id,
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        invalid_at=invalid_at,
        expired_at=expired_at,
        supersedes=supersedes,
        supersedes_reason=supersedes_reason,
        cognitive_type=cognitive_type,
        source_type=source_type,
        title=title,
        summary=summary,
    )


def _note(ep_id: str = "e1", **kwargs) -> ParsedNote:
    e = _episode(ep_id, **kwargs)
    return ParsedNote(episode=e, file_path=f"notes/{ep_id}.md", mtime_ms=1000, size=42)


def _index_rows(sidecar: SqliteSidecarIndexRepository) -> list[dict]:
    rows = sidecar._cm.writer.execute(  # noqa: SLF001
        "SELECT ep_id, fact_id, subject, file_path, mtime_ms, size, title, summary, "
        "skip_extraction, supersedes, supersedes_reason, invalid_at, expired_at "
        "FROM episode_index ORDER BY ep_id"
    ).fetchall()
    return [dict(r) for r in rows]


def test_structurally_satisfies_protocol(sidecar: SqliteSidecarIndexRepository) -> None:
    assert isinstance(sidecar, SidecarIndexRepository)
    assert not hasattr(sidecar, "atomic")  # SO-7a.6


def test_put_then_get_path(sidecar: SqliteSidecarIndexRepository) -> None:
    sidecar.put_path("e1", "notes/e1.md", 111, 42)
    assert sidecar.get_path("e1") == ("notes/e1.md", 111, 42)


def test_get_path_missing_returns_none(sidecar: SqliteSidecarIndexRepository) -> None:
    assert sidecar.get_path("nope") is None


def test_put_path_upsert_on_rename(sidecar: SqliteSidecarIndexRepository) -> None:
    # a rename is an UPDATE (episode_paths is mutable); the UPSERT keeps one row.
    sidecar.put_path("e1", "old.md", 1, 10)
    sidecar.put_path("e1", "new.md", 2, 20)
    assert sidecar.get_path("e1") == ("new.md", 2, 20)


def test_reindex_commits_metadata_with_body(sidecar: SqliteSidecarIndexRepository) -> None:
    # the reindex context commits the path metadata alongside the caller's work;
    # a body exception leaves the metadata uncommitted (atomic rollback).
    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), sidecar.reindex("e1", "notes/e1.md", 5, 50):
        raise _Boom
    assert sidecar.get_path("e1") is None


def test_reindex_success_persists(sidecar: SqliteSidecarIndexRepository) -> None:
    with sidecar.reindex("e1", "notes/e1.md", 5, 50):
        pass
    assert sidecar.get_path("e1") == ("notes/e1.md", 5, 50)


# --- rebuild_all (clear-then-rebuild, ruamel-free, B3=(i) austere) ------------


def test_rebuild_all_populates_index_and_paths(sidecar: SqliteSidecarIndexRepository) -> None:
    report = sidecar.rebuild_all([_note("e1", fact_id="f1"), _note("e2", fact_id="f2")])
    assert report.indexed == 2
    assert report.skipped == []
    rows = {r["ep_id"]: r for r in _index_rows(sidecar)}
    assert set(rows) == {"e1", "e2"}
    # file metadata + title/summary denormalized into episode_index (003 SO-1).
    assert rows["e1"]["file_path"] == "notes/e1.md"
    assert rows["e1"]["mtime_ms"] == 1000
    assert rows["e1"]["size"] == 42
    assert rows["e1"]["title"] == "Title"
    assert rows["e1"]["summary"] == "Summary"
    # episode_paths populated too.
    assert sidecar.get_path("e1") == ("notes/e1.md", 1000, 42)
    assert sidecar.get_path("e2") == ("notes/e2.md", 1000, 42)


def test_rebuild_all_clear_then_rebuild_replaces(sidecar: SqliteSidecarIndexRepository) -> None:
    # clear-then-rebuild (not upsert): a second rebuild wipes the old index rows,
    # so a vault deletion propagates without a diff (.md is the source of truth).
    sidecar.rebuild_all([_note("e1", fact_id="f1"), _note("e2", fact_id="f2")])
    sidecar.rebuild_all([_note("e3", fact_id="f3")])
    rows = {r["ep_id"] for r in _index_rows(sidecar)}
    assert rows == {"e3"}  # e1/e2 gone, e3 is the only row
    assert sidecar.get_path("e1") is None
    assert sidecar.get_path("e2") is None  # both prior paths wiped (clear-then-rebuild)


def test_rebuild_all_derives_skip_extraction_from_provenance(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # extraction_mode=skip (migrator default) -> skip_extraction=1 (ADR-09).
    # extraction_mode=llm -> skip_extraction=0.
    sidecar.rebuild_all(
        [_note("e1", fact_id="f1", extraction_mode="skip"),
         _note("e2", fact_id="f2", extraction_mode="llm")]
    )
    rows = {r["ep_id"]: r["skip_extraction"] for r in _index_rows(sidecar)}
    assert rows == {"e1": 1, "e2": 0}


def test_rebuild_all_reports_duplicate_vigent_fact_id_conflict(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # two vigent notes with the same fact_id violate I11. Rebuild does NOT
    # auto-pick a winner: ALL members of the conflict group are skipped + reported
    # (ADR-10 honesty). The index stays empty for that fact_id.
    report = sidecar.rebuild_all(
        [_note("e1", fact_id="dup"), _note("e2", fact_id="dup")]
    )
    assert report.indexed == 0
    assert {c.ep_id for c in report.skipped} == {"e1", "e2"}
    assert all(c.fact_id == "dup" for c in report.skipped)
    assert all(c.reason for c in report.skipped)
    # nothing landed in the index or paths.
    assert _index_rows(sidecar) == []
    assert sidecar.get_path("e1") is None


def test_rebuild_all_vigent_and_invalidated_same_fact_id_no_conflict(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # I11 only fires when BOTH rows are vigent. A vigent successor + an
    # invalidated predecessor sharing a fact_id is a legitimate supersession,
    # not a conflict — both land in the index.
    report = sidecar.rebuild_all(
        [
            _note("e0", fact_id="f1", invalid_at=datetime(2026, 1, 5, tzinfo=UTC)),
            _note("e1", fact_id="f1", supersedes="e0"),
        ]
    )
    assert report.indexed == 2
    assert report.skipped == []
    assert {r["ep_id"] for r in _index_rows(sidecar)} == {"e0", "e1"}


def test_rebuild_all_does_not_touch_episodes(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # B3=(i) austere: rebuild populates the index cache only; it NEVER writes the
    # append-only episodes table (that is the engine's hot-path cache via remember).
    sidecar.rebuild_all([_note("e1", fact_id="f1")])
    count = sidecar._cm.writer.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM episodes"
    ).fetchone()[0]
    assert count == 0


def test_rebuild_all_round_trips_supersedes_reason_via_index(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # the portable supersedes_reason (migration 009) lands in episode_index via
    # rebuild_all, not just the episodes table via append.
    sidecar.rebuild_all(
        [
            _note("e0", fact_id="f1", invalid_at=datetime(2026, 1, 5, tzinfo=UTC)),
            _note("e1", fact_id="f1", supersedes="e0", supersedes_reason="correction"),
        ]
    )
    rows = {r["ep_id"]: r["supersedes_reason"] for r in _index_rows(sidecar)}
    assert rows["e0"] is None
    assert rows["e1"] == "correction"


def test_rebuild_all_null_fact_id_notes_never_conflict(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # a note with fact_id=None (no derivable subject) is not indexed by fact_id;
    # two such vigent notes coexist (SQLite treats NULLs as distinct under the
    # I11 partial unique index), so neither is a conflict.
    sidecar.rebuild_all([_note("e1", fact_id=None), _note("e2", fact_id=None)])
    rows = {r["ep_id"] for r in _index_rows(sidecar)}
    assert rows == {"e1", "e2"}  # both landed, no conflict group


def test_rebuild_all_empty_notes_clears_prior_rows(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # rebuild_all([]) still runs the clear: prior index + paths are wiped, leaving
    # an empty index (pinning the clear-then-rebuild contract on the empty case).
    sidecar.rebuild_all([_note("e1", fact_id="f1"), _note("e2", fact_id="f2")])
    report = sidecar.rebuild_all([])
    assert report.indexed == 0
    assert report.skipped == []
    assert _index_rows(sidecar) == []
    assert sidecar.get_path("e1") is None
    assert sidecar.get_path("e2") is None


def test_rebuild_all_reports_duplicate_ep_id_conflict(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # two notes carrying the SAME ep_id (duplicate id in the vault) would hit the
    # PRIMARY KEY mid-rebuild. The pre-pass screens it and reports a structured
    # RebuildConflict instead of raising an opaque IntegrityError (ADR-10: the
    # operator gets a report, not a crash). Both are skipped.
    report = sidecar.rebuild_all(
        [
            ParsedNote(
                episode=_episode("dup-id", fact_id="f1"),
                file_path="notes/a.md",
                mtime_ms=1,
                size=1,
            ),
            ParsedNote(
                episode=_episode("dup-id", fact_id="f2"),
                file_path="notes/b.md",
                mtime_ms=2,
                size=2,
            ),
        ]
    )
    assert report.indexed == 0
    assert len(report.skipped) == 2
    assert {c.ep_id for c in report.skipped} == {"dup-id"}
    assert all(c.reason == "duplicate-ep_id" for c in report.skipped)
    assert {c.file_path for c in report.skipped} == {"notes/a.md", "notes/b.md"}
    assert _index_rows(sidecar) == []


def test_sidecar_module_is_ruamel_free() -> None:
    # ruamel-confinement invariant: the sidecar (core) must NOT import ruamel.yaml
    # nor python-frontmatter. The codec is confined to frontmatter.handler/
    # frontmatter.adapter; rebuild_all receives ruamel-free ParsedNote payloads.
    # Inspect the import AST (not the source text) so the docstring that
    # documents the invariant does not trip a naive substring check.
    import ast
    from pathlib import Path

    from seahorse.persistence import sqlite_sidecar as _mod

    tree = ast.parse(Path(_mod.__file__).read_text(encoding="utf-8"))
    forbidden_modules = {"ruamel", "ruamel.yaml", "yaml", "frontmatter", "python_frontmatter"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    leaked = imported & forbidden_modules
    assert not leaked, f"sidecar imports forbidden codec modules: {leaked}"


def test_rebuild_all_atomic_rollback_on_failure(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # the clear + repopulate is ONE atomic. A note that passes the pre-pass
    # (no duplicate fact_id) but violates a DB CHECK (valid_at > invalid_at)
    # raises mid-insert; the atomic rolls back the DELETE too, so the prior
    # index state is preserved (not half-wiped).
    sidecar.rebuild_all([_note("e1", fact_id="f1")])
    pre = _index_rows(sidecar)
    bad = ParsedNote(
        episode=Episode(
            id="e2",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            schema_version="3.1",
            provenance={"extraction_mode": "skip"},
            body="body",
            subject="S",
            fact_id="f2",
            valid_at=datetime(2026, 1, 10, tzinfo=UTC),
            invalid_at=datetime(2026, 1, 5, tzinfo=UTC),  # valid_at > invalid_at -> CHECK
            cognitive_type="fact",
            source_type="agent",
        ),
        file_path="notes/e2.md",
        mtime_ms=1,
        size=1,
    )
    with pytest.raises(sqlite3.IntegrityError):  # CHECK constraint (valid_at <= invalid_at)
        sidecar.rebuild_all([bad, _note("e3", fact_id="f3")])
    assert _index_rows(sidecar) == pre  # prior state preserved (DELETE rolled back)


# --- rebuild_all secondary-index wipe seam (C8.8 / #9 forward-compat) --------
#
# rebuild_all clears episode_index + episode_paths but NOT the FTS5/vec0 tables.
# Once #6 materializes them (MVP-1), a vault rebuild would leave the FTS/vec
# indexes pointing at ep_ids deleted from episode_index = ghost hits. The seam:
# rebuild_all runs caller-supplied secondary-index wipe hooks inside the SAME
# atomic (clear phase, after the two DELETEs, before repopulate), so the
# secondary indexes are cleared in the same transaction — no half-wiped ghost
# state. MVP-0 passes no hooks (the FTS5/vec0 tables do not exist yet, so there
# is nothing to wipe and no ghost hits); the seam is in place for MVP-1 to plug.


def test_rebuild_all_runs_secondary_wipes_inside_atomic(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # the wipe hook receives the writer connection and observes episode_index
    # ALREADY cleared (the clear phase runs the wipe after the two DELETEs, so
    # the secondary index wipe sees the same empty episode_index the repopulate
    # is about to refill). Pins the seam: the hook is called once, with a
    # connection, inside the atomic, in the clear phase.
    sidecar.rebuild_all([_note("e1", fact_id="f1")])  # seed a prior row
    seen: dict[str, object] = {}

    def wipe(conn: sqlite3.Connection) -> None:
        seen["called"] = True
        # episode_index is empty here (DELETE ran before the wipe, repopulate after)
        seen["index_count_at_wipe"] = conn.execute(
            "SELECT COUNT(*) FROM episode_index"
        ).fetchone()[0]

    sidecar.rebuild_all([_note("e2", fact_id="f2")], secondary_index_wipes=[wipe])
    assert seen["called"] is True
    assert seen["index_count_at_wipe"] == 0  # wipe runs in the clear phase


def test_rebuild_all_secondary_wipe_failure_rolls_back_clear(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # C8.8 correctness core: the secondary-index wipe shares the rebuild atomic.
    # If a wipe raises, the episode_index/episode_paths DELETE rolls back too —
    # the prior index is preserved (not half-wiped with FTS/vec stale). This is
    # the property that makes the coordinated wipe safe: a failed secondary
    # wipe cannot leave episode_index empty while the secondary index still
    # holds stale rows (the whole clear rolls back together).
    sidecar.rebuild_all([_note("e1", fact_id="f1")])  # prior state
    pre = _index_rows(sidecar)
    assert pre  # sanity: there is a prior row to preserve

    def boom(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("secondary index wipe failed")

    with pytest.raises(RuntimeError, match="secondary index wipe failed"):
        sidecar.rebuild_all([_note("e2", fact_id="f2")], secondary_index_wipes=[boom])
    assert _index_rows(sidecar) == pre  # prior state preserved (DELETE rolled back)


def test_rebuild_all_multiple_secondary_wipes_run_in_order_before_repopulate(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # multiple wipe hooks (FTS + vec in MVP-1) run in sequence, all in the clear
    # phase (before the repopulate loop). The second wipe still sees episode_index
    # empty, proving both wipes ran before any repopulate INSERT.
    order: list[str] = []

    def wipe_a(conn: sqlite3.Connection) -> None:
        order.append("a")
        order.append(f"a_count={conn.execute('SELECT COUNT(*) FROM episode_index').fetchone()[0]}")

    def wipe_b(conn: sqlite3.Connection) -> None:
        order.append("b")
        order.append(f"b_count={conn.execute('SELECT COUNT(*) FROM episode_index').fetchone()[0]}")

    sidecar.rebuild_all(
        [_note("e1", fact_id="f1"), _note("e2", fact_id="f2")],
        secondary_index_wipes=[wipe_a, wipe_b],
    )
    assert order == ["a", "a_count=0", "b", "b_count=0"]  # both ran, clear phase, in order


def test_rebuild_all_default_no_secondary_wipes_is_noop(
    sidecar: SqliteSidecarIndexRepository,
) -> None:
    # MVP-0 contract: the default (no wipes) is a pure no-op — rebuild_all behaves
    # exactly as before. A sentinel wipe that would raise if called proves the
    # default path never invokes it (the FTS5/vec0 tables do not exist yet, so
    # there is genuinely nothing to wipe in MVP-0).
    def must_not_be_called(_conn: sqlite3.Connection) -> None:
        raise AssertionError("secondary wipe must not run when none are passed")

    report = sidecar.rebuild_all(
        [_note("e1", fact_id="f1")], secondary_index_wipes=[]
    )
    assert report.indexed == 1
    assert report.skipped == []
    # explicit default (no kwarg) also works — pins the backward-compatible sig.
    report2 = sidecar.rebuild_all([_note("e2", fact_id="f2")])
    assert report2.indexed == 1
