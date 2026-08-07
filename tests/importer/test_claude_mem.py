"""Tests for the claude-mem importer pure mapping + reader (#15, f5-15 §2).

Signals:
- ``import_record`` is pure: same record -> same ImporterResult (no state, no
  store, no LLM, no conflict resolution).
- Body has H1 = title (engine derives subject, SO-2); the importer guarantees a
  body with H1 (f5-15 §8.2).
- ``valid_at`` = the observation's ``created_at`` (importer editorial authority,
  I2/SO-4a).
- ``cognitive_type`` conservative heuristic (f5-15 §6.5).
- Provenance carries the importer contract (source_type=importer,
  importer_vendor=claude-mem, extraction_mode=skip) + the vendor id.
- Id is a deterministic UUIDv5 (SO-4b) — re-import yields the same id.
- Loss report documents every loss/synthesis (auditable).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

from seahorse.engine.canonical import canonical_body_hash
from seahorse.engine.ids import deterministic_id
from seahorse.importer import ClaudeMemReader, import_record

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _insert(
    conn, i: int, project: str, typ: str, title: str, narr: str, ts: str, agent: str
) -> None:
    conn.execute(
        "INSERT INTO observations (id, project, type, title, narrative, created_at, agent_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (i, project, typ, title, narr, ts, agent),
    )


def _record(**over) -> dict:
    base = {
        "id": 42,
        "project": "seahorse",
        "type": "decision",
        "title": "Un-reserving extraction_mode",
        "narrative": "For the v0.2.0 milestone, extraction_mode=consolidated is un-reserved.",
        "facts": '["fact1"]',
        "concepts": '["how-it-works"]',
        "created_at": "2026-08-06T20:40:45.420Z",
        "agent_id": "claude-code",
    }
    base.update(over)
    return base


class TestImportRecord:
    def test_maps_to_one_episode(self) -> None:
        result = import_record(_record(), "claude-mem")
        assert len(result["notes"]) == 1
        ep = result["notes"][0]
        assert ep.body.startswith("# Un-reserving extraction_mode")
        assert ep.valid_at == datetime(2026, 8, 6, 20, 40, 45, 420000, tzinfo=UTC)

    def test_body_has_h1_title(self) -> None:
        ep = import_record(_record(), "claude-mem")["notes"][0]
        assert ep.body.splitlines()[0] == "# Un-reserving extraction_mode"

    def test_provenance_importer_contract(self) -> None:
        ep = import_record(_record(), "claude-mem")["notes"][0]
        prov = ep.provenance
        assert prov["source_type"] == "importer"
        assert prov["importer_vendor"] == "claude-mem"
        assert prov["extraction_mode"] == "skip"
        assert prov["model_used"] is None
        assert prov["prompt_hash"] is None
        assert prov["confidence"] == 1.0
        assert prov["source_record_id"] == "42"
        assert prov["x-claude-mem-source-id"] == "42"

    def test_deterministic_uuidv5_id(self) -> None:
        ep = import_record(_record(), "claude-mem")["notes"][0]
        expected = deterministic_id(
            "claude-mem", "42", canonical_body_hash(ep.body or "")
        )
        assert ep.id == expected
        assert uuid.UUID(ep.id).version == 5

    def test_pure_function_same_input_same_output(self) -> None:
        r1 = import_record(_record(), "claude-mem")
        r2 = import_record(_record(), "claude-mem")
        assert r1 == r2

    def test_cognitive_type_heuristic(self) -> None:
        def _ct(obs_type: str) -> str:
            return import_record(_record(type=obs_type), "claude-mem")["notes"][0].cognitive_type

        assert _ct("decision") == "semantic"
        assert _ct("feature") == "semantic"
        assert _ct("discovery") == "episodic"
        assert _ct("change") == "episodic"
        assert _ct("unknown") == "semantic"

    def test_loss_report_always_present(self) -> None:
        result = import_record(_record(), "claude-mem")
        loss = result["loss_report"]
        assert loss["vendor"] == "claude-mem"
        assert loss["source_record_id"] == "42"
        assert any("created_at" in f for f in loss["fields_lost"])
        assert any("cognitive_type" in f for f in loss["fields_synthesized"])
        assert any("facts" in f for f in loss["fields_lost"])

    def test_unsupported_vendor_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            import_record(_record(), "mem0")

    def test_missing_title_falls_back_to_type(self) -> None:
        ep = import_record(_record(title=""), "claude-mem")["notes"][0]
        assert ep.body.splitlines()[0] == "# decision"

    def test_missing_created_at_falls_back_to_now(self) -> None:
        ep = import_record(_record(created_at=None), "claude-mem")["notes"][0]
        assert ep.valid_at is None  # engine sets now at write time


class TestClaudeMemReader:
    def test_reads_observations_from_db(self, tmp_path) -> None:
        db = tmp_path / "claude-mem.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE observations (id INTEGER PRIMARY KEY, project TEXT, "
                "type TEXT, title TEXT, narrative TEXT, created_at TEXT, agent_id TEXT)"
            )
            _insert(conn, 1, "seahorse", "decision", "T", "N", "2026-08-01T00:00:00Z", "a1")
            _insert(conn, 2, "other", "feature", "T2", "N2", "2026-08-02T00:00:00Z", "a2")
        reader = ClaudeMemReader(db)
        rows = reader.iter_observations()
        assert len(rows) == 2
        assert rows[0]["id"] == 1

    def test_filters_by_project(self, tmp_path) -> None:
        db = tmp_path / "claude-mem.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE observations (id INTEGER PRIMARY KEY, project TEXT, "
                "type TEXT, title TEXT, narrative TEXT, created_at TEXT, agent_id TEXT)"
            )
            _insert(conn, 1, "seahorse", "decision", "T", "N", "2026-08-01T00:00:00Z", "a1")
            _insert(conn, 2, "other", "feature", "T2", "N2", "2026-08-02T00:00:00Z", "a2")
        reader = ClaudeMemReader(db)
        rows = reader.iter_observations(project="seahorse")
        assert len(rows) == 1
        assert rows[0]["id"] == 1

    def test_missing_db_raises(self, tmp_path) -> None:
        reader = ClaudeMemReader(tmp_path / "nope.db")
        with pytest.raises(FileNotFoundError):
            reader.iter_observations()
