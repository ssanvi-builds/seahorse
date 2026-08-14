"""Tests for the adapters + registry."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from seahorse.benchmark.adapters.base import parse_date
from seahorse.benchmark.adapters.longmemeval import LMEBLoader
from seahorse.benchmark.adapters.registry import AdapterRegistry
from seahorse.benchmark.config import BenchmarkConfig


def test_registry_register_get_list():
    class _FakeLoader:
        @staticmethod
        def load(config):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        @staticmethod
        def name() -> str:
            return "fake"

        @staticmethod
        def available_configs() -> tuple[str, ...]:
            return ("s",)

    AdapterRegistry.register("fake")(_FakeLoader)
    assert AdapterRegistry.get("fake") is _FakeLoader
    assert "fake" in AdapterRegistry.list()


def test_registry_get_unknown_raises():
    with pytest.raises(KeyError, match="Unknown benchmark adapter"):
        AdapterRegistry.get("does-not-exist")


def test_lmeb_is_registered():
    assert AdapterRegistry.get("lmeb") is LMEBLoader
    assert LMEBLoader.name() == "LongMemEval"
    assert LMEBLoader.available_configs() == ("s",)


def test_lmeb_from_row_maps_fields():
    """Real LMEB row shape: sessions are turn-lists parallel to session_ids/dates."""
    row = {
        "question_id": "q1",
        "question": "What is the capital of France?",
        "answer": "Paris",
        "answer_session_ids": ["s1"],
        "question_type": "single-session-user",
        "question_date": "2023/05/30 (Tue) 23:40",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 10:00"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "What is the capital of France?"},
                {"role": "assistant", "content": "It is Paris."},
            ],
            [{"role": "user", "content": "And the population?"}],
        ],
    }
    inst = LMEBLoader._from_row(row)
    assert inst.instance_id == "q1"
    assert inst.golden_answer == "Paris"
    assert inst.golden_session_ids == ("s1",)
    assert inst.capabilities == ("information-extraction",)
    assert inst.cognitive_category == "episodic"
    assert inst.question_date == datetime(2023, 5, 30, 23, 40, tzinfo=UTC)
    assert inst.abstention is False
    # Canonical haystack: {session_id, date, turns:[{body,...}]} — the shape
    # CorpusBuilder/SeahorseSUT.ingest consume.
    assert len(inst.haystack) == 2
    s1, s2 = inst.haystack
    assert s1["session_id"] == "s1"
    assert s1["date"] == datetime(2023, 5, 20, 2, 21, tzinfo=UTC)
    assert s1["turns"][0]["body"] == "What is the capital of France?"
    assert s1["turns"][1]["body"] == "It is Paris."
    assert s2["session_id"] == "s2"
    assert s2["date"] == datetime(2023, 5, 21, 10, 0, tzinfo=UTC)
    assert s2["turns"][0]["body"] == "And the population?"


def test_lmeb_from_row_numeric_answer_normalized():
    """LongMemEval answers are mixed (468 str + 32 int); the canonical
    ``golden_answer: str | None`` contract requires normalization."""
    row = {
        "question_id": "q_num",
        "question": "How many items?",
        "answer": 3,
        "answer_session_ids": ["s1"],
        "question_type": "single-session-user",
        "haystack_sessions": [],
        "haystack_session_ids": [],
        "haystack_dates": [],
    }
    inst = LMEBLoader._from_row(row)
    assert inst.golden_answer == "3"


def test_lmeb_turns_get_derivable_title():
    """Conversational LMEB turns (no H1) need a title for the skip path's
    subject derivation — ``deterministic_extract`` raises
    ``SubjectDerivationError`` otherwise (verified on the real S snapshot)."""
    row = {
        "question_id": "q1",
        "question": "Q?",
        "answer": "A",
        "answer_session_ids": ["s1"],
        "question_type": "single-session-user",
        "haystack_session_ids": ["s1"],
        "haystack_dates": ["2023/05/20 (Sat) 02:21"],
        "haystack_sessions": [
            [{"role": "user", "content": "The farmer needs to transport a fox across a river."}]
        ],
    }
    inst = LMEBLoader._from_row(row)
    turn = inst.haystack[0]["turns"][0]
    assert turn["title"]  # non-empty → the skip path can derive a subject


def test_lmeb_split_name_for_config():
    assert LMEBLoader._split_name("s") == "longmemeval_s_cleaned"
    assert LMEBLoader._split_name("m") == "longmemeval_m_cleaned"


def test_lmeb_abstention_detection():
    row = {
        "question_id": "q5_abs",
        "question": "Is there any info about the weather?",
        "answer": "No",
        "answer_session_ids": [],
        "question_type": "abstention",
        "haystack_sessions": [],
    }
    inst = LMEBLoader._from_row(row)
    assert inst.abstention is True
    assert inst.cognitive_category == "n/a"
    assert inst.capabilities == ("abstention",)


def test_lmeb_cognitive_mapping():
    assert LMEBLoader._from_row(
        {
            "question_id": "q2",
            "question": "Q?",
            "answer": "A",
            "answer_session_ids": [],
            "question_type": "knowledge-update",
            "haystack_sessions": [],
        }
    ).cognitive_category == "semantic"
    assert LMEBLoader._from_row(
        {
            "question_id": "q3",
            "question": "Q?",
            "answer": "A",
            "answer_session_ids": [],
            "question_type": "multi-session",
            "haystack_sessions": [],
        }
    ).capabilities == ("multi-session-reasoning",)


def test_parse_date_variants():
    assert parse_date(None) is None
    assert parse_date("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_date("2026-01-01T00:00:00+00:00") == datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_date(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_date("not-a-date") is None


def test_parse_date_longmemeval_format():
    """LongMemEval dates are ``YYYY/MM/DD (Weekday) HH:MM``."""
    assert parse_date("2023/05/30 (Tue) 23:40") == datetime(2023, 5, 30, 23, 40, tzinfo=UTC)
    assert parse_date("2023/05/20 (Sat) 02:21") == datetime(2023, 5, 20, 2, 21, tzinfo=UTC)


def test_lmeb_load_from_local_raw_json(tmp_path, monkeypatch):
    """load() parses a raw LongMemEval JSON snapshot with stdlib json — no
    ``trust_remote_code``, no datasets JSON pipeline (pyarrow block_size /
    mixed-type incompatibilities — the raw JSON is prematerialized)."""
    raw = [
        {
            "question_id": "q1",
            "question": "What degree did I graduate with?",
            "answer": "Business Administration",
            "answer_session_ids": ["answer_280352e9"],
            "question_type": "single-session-user",
            "question_date": "2023/05/30 (Tue) 23:40",
            "haystack_session_ids": ["sharegpt_yywfIrx_0"],
            "haystack_dates": ["2023/05/20 (Sat) 02:21"],
            "haystack_sessions": [
                [{"role": "user", "content": "What degree?"}],
            ],
        },
        {
            "question_id": "q2",
            "question": "What was the answer?",
            "answer": 3,
            "answer_session_ids": ["s2"],
            "question_type": "temporal-reasoning",
            "question_date": "2023/05/31 (Wed) 09:15",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/30 (Tue) 23:40"],
            "haystack_sessions": [
                [{"role": "user", "content": "Old fact"}],
                [{"role": "user", "content": "New fact"}],
            ],
        },
    ]
    raw_file = tmp_path / "longmemeval_s_cleaned.json"
    raw_file.write_text(json.dumps(raw), encoding="utf-8")

    monkeypatch.setattr(
        "seahorse.benchmark.adapters.longmemeval._resolve_raw_json_path",
        lambda config: raw_file,
    )
    ds = LMEBLoader.load(BenchmarkConfig(dataset_config="s"))
    assert ds.name == "longmemeval-s"
    assert len(ds.instances) == 2
    assert ds.instances[0].golden_answer == "Business Administration"
    assert ds.instances[1].golden_answer == "3"
    assert ds.instances[1].question_date == datetime(2023, 5, 31, 9, 15, tzinfo=UTC)
    assert len(ds.instances[1].haystack) == 2
    # split_hash is deterministic for the same content.
    ds2 = LMEBLoader.load(BenchmarkConfig(dataset_config="s"))
    assert ds2.split_hash == ds.split_hash


def test_lmeb_loader_is_dataset_loader_protocol():
    assert isinstance(LMEBLoader, type)
    assert hasattr(LMEBLoader, "load")
    assert hasattr(LMEBLoader, "name")
    assert hasattr(LMEBLoader, "available_configs")


def test_lmeb_load_raises_without_benchmark_extra(monkeypatch):
    """Without the 'benchmark' extra, load() raises a clear RuntimeError."""
    import importlib

    def fake_import(name):
        raise ImportError("no benchmark extra")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(RuntimeError, match="install seahorse\\[benchmark\\]"):
        LMEBLoader.load(BenchmarkConfig())
