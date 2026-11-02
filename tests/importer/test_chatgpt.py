"""Tests for the chatgpt importer pure mapping + reader.

Signals:
- ``import_record`` is pure: same record -> same ImporterResult.
- ``memory`` -> semantic episode with H1 = truncated text; ``conversation`` ->
  episodic episode with a BOUNDED body (metadata + first user message, never the
  full transcript).
- ``valid_at`` = the vendor timestamp (importer editorial authority).
- Provenance carries the importer contract (source_type=importer,
  importer_vendor=chatgpt, extraction_mode=skip) + the vendor id.
- Id is a deterministic UUIDv5 — re-import yields the same id.
- Loss report documents the un-inlined transcript (conversation) and unmapped
  fields (memory).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from seahorse.engine.canonical import canonical_body_hash
from seahorse.engine.ids import deterministic_id
from seahorse.importer import ChatGPTReader, import_record

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _memory(**over) -> dict:
    base = {
        "kind": "memory",
        "id": "mem-1",
        "text": "Sergio prefers learning by building: one hour of video equals two hours of code.",
        "created_at": datetime(2026, 8, 6, 20, 40, 45, tzinfo=UTC),
    }
    base.update(over)
    return base


def _conversation(**over) -> dict:
    base = {
        "kind": "conversation",
        "id": "conv-42",
        "title": "Designing the bi-temporal index",
        "create_time": datetime(2026, 8, 6, 10, 0, tzinfo=UTC),
        "update_time": datetime(2026, 8, 6, 11, 0, tzinfo=UTC),
        "message_count": 12,
        "first_user_text": "How should we index valid_at for point-in-time queries?",
    }
    base.update(over)
    return base


class TestImportRecord:
    def test_memory_maps_to_one_semantic_episode(self) -> None:
        result = import_record(_memory(), "chatgpt")
        assert len(result["notes"]) == 1
        ep = result["notes"][0]
        assert ep.cognitive_type == "semantic"
        assert ep.body.startswith("# Sergio prefers learning by building")

    def test_memory_valid_at_is_vendor_timestamp(self) -> None:
        ep = import_record(_memory(), "chatgpt")["notes"][0]
        assert ep.valid_at == datetime(2026, 8, 6, 20, 40, 45, tzinfo=UTC)

    def test_conversation_maps_to_episodic(self) -> None:
        ep = import_record(_conversation(), "chatgpt")["notes"][0]
        assert ep.cognitive_type == "episodic"
        assert ep.body.splitlines()[0] == "# Designing the bi-temporal index"

    def test_conversation_body_is_bounded_not_full_transcript(self) -> None:
        ep = import_record(_conversation(), "chatgpt")["notes"][0]
        assert "message_count: 12" in ep.body
        assert "create_time:" in ep.body
        # The full transcript is never inlined — only the first user message.
        assert "first_user_message:" in ep.body
        assert len(ep.body) < 500

    def test_provenance_importer_contract(self) -> None:
        ep = import_record(_memory(), "chatgpt")["notes"][0]
        prov = ep.provenance
        assert prov["source_type"] == "importer"
        assert prov["importer_vendor"] == "chatgpt"
        assert prov["extraction_mode"] == "skip"
        assert prov["model_used"] is None
        assert prov["prompt_hash"] is None
        assert prov["confidence"] == 1.0
        assert prov["source_record_id"] == "mem-1"
        assert prov["x-chatgpt-source-id"] == "mem-1"

    def test_deterministic_uuidv5_id(self) -> None:
        ep = import_record(_memory(), "chatgpt")["notes"][0]
        expected = deterministic_id("chatgpt", "mem-1", canonical_body_hash(ep.body or ""))
        assert ep.id == expected
        assert uuid.UUID(ep.id).version == 5

    def test_pure_function_same_input_same_output(self) -> None:
        assert import_record(_memory(), "chatgpt") == import_record(_memory(), "chatgpt")
        assert import_record(_conversation(), "chatgpt") == import_record(
            _conversation(), "chatgpt"
        )

    def test_loss_report_documents_transcript_not_inlined(self) -> None:
        loss = import_record(_conversation(), "chatgpt")["loss_report"]
        assert loss["vendor"] == "chatgpt"
        assert any("transcript" in f for f in loss["fields_lost"])
        assert any("mapping" in f for f in loss["fields_lost"])

    def test_loss_report_always_present_for_memory(self) -> None:
        loss = import_record(_memory(), "chatgpt")["loss_report"]
        assert loss["source_record_id"] == "mem-1"
        assert any("created_at" in f for f in loss["fields_lost"])

    def test_unsupported_vendor_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            import_record(_memory(), "mem0")

    def test_missing_timestamp_falls_back(self) -> None:
        ep = import_record(_memory(created_at=None), "chatgpt")["notes"][0]
        assert ep.valid_at is None  # engine sets now at write time


class TestChatGPTReader:
    def _write_export(self, tmp_path, *, memories, conversations) -> None:
        (tmp_path / "memory.json").write_text(json.dumps(memories))
        (tmp_path / "conversations.json").write_text(json.dumps(conversations))

    def test_reads_memories_and_conversations(self, tmp_path) -> None:
        self._write_export(
            tmp_path,
            memories=[{"id": "m1", "text": "A saved memory", "created_at": 1750000000}],
            conversations=[
                {
                    "id": "c1",
                    "title": "A chat",
                    "create_time": 1750000000,
                    "update_time": 1750000100,
                    "mapping": {
                        "n1": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["hello"]},
                                "create_time": 1750000000,
                            }
                        },
                        "n2": {
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["hi there"]},
                                "create_time": 1750000001,
                            }
                        },
                    },
                }
            ],
        )
        records = ChatGPTReader(tmp_path).iter_records()
        assert len(records) == 2
        assert records[0]["kind"] == "memory"
        assert records[0]["text"] == "A saved memory"
        assert records[1]["kind"] == "conversation"
        assert records[1]["message_count"] == 2
        assert records[1]["first_user_text"] == "hello"

    def test_skips_memory_without_text(self, tmp_path) -> None:
        self._write_export(
            tmp_path,
            memories=[{"id": "m1", "text": ""}, {"id": "m2", "text": "kept"}],
            conversations=[],
        )
        records = ChatGPTReader(tmp_path).iter_records()
        assert len(records) == 1
        assert records[0]["id"] == "m2"

    def test_missing_files_yield_no_records(self, tmp_path) -> None:
        assert ChatGPTReader(tmp_path).iter_records() == []

    def test_memory_dict_wrapper_tolerated(self, tmp_path) -> None:
        self._write_export(
            tmp_path,
            memories={"memories": [{"text": "wrapped"}]},
            conversations=[],
        )
        records = ChatGPTReader(tmp_path).iter_records()
        assert len(records) == 1
        assert records[0]["text"] == "wrapped"
