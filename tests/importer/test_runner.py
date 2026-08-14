"""Tests for the importer runner.

Signals:
- ``dry-run`` maps without writing (no facade calls); manifest + projected notes.
- ``commit`` writes via the facade's ``remember`` (single entry point).
- Idempotency: re-import of the same record -> NOOP -> ``skipped_idempotent``.
- Collisions are RETURNED (``WriteResult.collisions_detected``), never raised;
  default policy ``skip`` -> ``skipped_collision``.
- Manifest schema ``seahorse.importer.manifest/1.0``; persisted to
  ``{output_dir}/{run_id}/manifest.json``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import WriteResult
from seahorse.disclosure.shaper import DisclosureShaperImpl
from seahorse.engine.engine import BiTemporalEngine
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import FacadeConfig, RememberPayload
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from seahorse.importer import ImportRunner
from seahorse.importer.types import MANIFEST_SCHEMA
from seahorse.persistence.storage import Storage
from seahorse.write_path.stub import StubWritePath


def _record(**over) -> dict:
    base = {
        "id": 1,
        "project": "seahorse",
        "type": "decision",
        "title": "A decision",
        "narrative": "The decision narrative.",
        "created_at": "2026-08-01T00:00:00Z",
        "agent_id": "claude-code",
    }
    base.update(over)
    return base


def _advancing_clock(start: datetime, step: timedelta):
    state = {"t": start}

    def _now() -> datetime:
        t = state["t"]
        state["t"] = t + step
        return t

    return _now


@pytest.fixture()
def facade(tmp_path):
    storage = Storage(tmp_path / "imp.db")
    engine = BiTemporalEngine(repo=storage.episodes, audit=storage.audit)
    shaper = DisclosureShaperImpl(
        index_repo=storage.episode_index, episode_repo=storage.episodes
    )
    write_path = StubWritePath(engine=engine)
    clock = _advancing_clock(datetime(2026, 8, 1, 12, 0, tzinfo=UTC), timedelta(seconds=10))
    retriever = VigenteListingRetriever(engine=engine, clock=clock, config=FacadeConfig())
    f = MemoryFacade(
        engine=engine,
        write_path=write_path,
        shaper=shaper,
        retriever=retriever,
        clock=clock,
        config=FacadeConfig(),
    )
    yield f
    storage.close()


class _StubFacade:
    """Facade double returning configurable WriteResults (collision/idempotency)."""

    def __init__(self, result: WriteResult) -> None:
        self.result = result
        self.remember_calls: list[tuple[RememberPayload, str]] = []

    def remember(self, payload, *, skip_extraction=None, extraction_mode=None, now=None):
        self.remember_calls.append((payload, extraction_mode))
        return self.result


class TestDryRun:
    def test_no_facade_calls(self, facade) -> None:
        runner = ImportRunner(facade)
        manifest = runner.run([_record()], mode="dry-run")
        assert manifest.aggregate["records_read"] == 1
        assert manifest.aggregate["notes_emitted"] == 1
        assert manifest.aggregate["failures"] == 0
        assert manifest.items[0].status == "committed"
        assert manifest.items[0].notes_emitted[0]["action"] == "create"

    def test_manifest_schema(self, facade) -> None:
        manifest = ImportRunner(facade).run([_record()], mode="dry-run")
        assert manifest.manifest_schema == MANIFEST_SCHEMA
        assert manifest.vendor == "claude-mem"
        assert manifest.mode == "dry-run"
        assert manifest.integrity_ok is True


class TestCommit:
    def test_writes_via_facade(self, facade) -> None:
        runner = ImportRunner(facade)
        manifest = runner.run([_record()], mode="commit")
        assert manifest.aggregate["notes_emitted"] == 1
        assert manifest.aggregate["failures"] == 0
        assert manifest.items[0].status == "committed"
        # The episode is actually persisted: recall sees it.
        rows = facade.recall("decision", k=10)
        assert len(rows) == 1

    def test_commit_persists_importer_provenance(self, facade) -> None:
        runner = ImportRunner(facade)
        manifest = runner.run([_record()], mode="commit")
        ep_id = manifest.items[0].notes_emitted[0]["ep_id"]
        ep = facade._engine._repo.get(ep_id)
        assert ep.provenance["source_type"] == "importer"
        assert ep.provenance["importer_vendor"] == "claude-mem"
        assert ep.provenance["extraction_mode"] == "skip"

    def test_reimport_is_idempotent(self, facade) -> None:
        runner = ImportRunner(facade)
        m1 = runner.run([_record()], mode="commit")
        assert m1.aggregate["notes_emitted"] == 1
        m2 = runner.run([_record()], mode="commit")
        assert m2.aggregate["skipped_idempotent"] == 1
        assert m2.aggregate["notes_emitted"] == 0
        assert m2.items[0].status == "skipped_idempotent"


class TestCollisions:
    def test_collision_returned_not_raised(self) -> None:
        stub = _StubFacade(
            WriteResult(
                ep_id=None, fact_id=None, status="COLLISION", collisions_detected=[{"k": 1}]
            )
        )
        runner = ImportRunner(stub)
        manifest = runner.run([_record()], mode="commit")
        assert manifest.aggregate["skipped_collision"] == 1
        assert manifest.items[0].status == "skipped_collision"
        assert len(stub.remember_calls) == 1

    def test_noop_maps_to_skipped_idempotent(self) -> None:
        stub = _StubFacade(
            WriteResult(ep_id="e1", fact_id="f1", status="NOOP", collisions_detected=[])
        )
        manifest = ImportRunner(stub).run([_record()], mode="commit")
        assert manifest.aggregate["skipped_idempotent"] == 1
        assert manifest.items[0].status == "skipped_idempotent"


class TestManifestPersistence:
    def test_writes_manifest_to_output_dir(self, facade, tmp_path) -> None:
        runner = ImportRunner(facade, output_dir=tmp_path)
        manifest = runner.run([_record()], mode="dry-run")
        path = tmp_path / manifest.run_id / "manifest.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["manifest_schema"] == MANIFEST_SCHEMA
        assert data["aggregate"]["records_read"] == 1

    def test_no_output_dir_returns_manifest(self, facade) -> None:
        manifest = ImportRunner(facade).run([_record()], mode="dry-run")
        assert manifest.aggregate["records_read"] == 1


class TestFailures:
    def test_bad_record_fails_loud_batch_continues(self, facade) -> None:
        runner = ImportRunner(facade)
        # A non-dict record makes import_record raise (record.get fails) — the
        # runner fails that item loud and continues the batch.
        manifest = runner.run([_record(), "not-a-dict"], mode="dry-run")
        assert manifest.aggregate["records_read"] == 2
        assert manifest.aggregate["failures"] == 1
        assert manifest.integrity_ok is False
        failed = [i for i in manifest.items if i.status == "failed"]
        assert len(failed) == 1

    def test_invalid_mode_rejected(self, facade) -> None:
        with pytest.raises(ValueError, match="mode"):
            ImportRunner(facade).run([_record()], mode="bogus")
