"""Tests for the adapters + registry (f5-16 §2.3/§4.1)."""

from __future__ import annotations

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
    row = {
        "question_id": "q1",
        "question": "What is the capital of France?",
        "answer": "Paris",
        "answer_session_ids": ["s1"],
        "question_type": "single-session-user",
        "question_date": "2026-01-01T00:00:00Z",
        "haystack_sessions": [{"session_id": "s1", "turns": []}],
    }
    inst = LMEBLoader._from_row(row)
    assert inst.instance_id == "q1"
    assert inst.golden_answer == "Paris"
    assert inst.golden_session_ids == ("s1",)
    assert inst.capabilities == ("information-extraction",)
    assert inst.cognitive_category == "episodic"
    assert inst.question_date == datetime(2026, 1, 1, tzinfo=UTC)
    assert inst.abstention is False


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


def test_lmeb_loader_is_dataset_loader_protocol():
    assert isinstance(LMEBLoader, type)
    assert hasattr(LMEBLoader, "load")
    assert hasattr(LMEBLoader, "name")
    assert hasattr(LMEBLoader, "available_configs")


def test_lmeb_load_raises_without_datasets(monkeypatch):
    """Without the 'benchmark' extra, load() raises a clear RuntimeError."""
    import importlib

    def fake_import(name):
        raise ImportError("no datasets")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(RuntimeError, match="install seahorse\\[benchmark\\]"):
        LMEBLoader.load(BenchmarkConfig())
