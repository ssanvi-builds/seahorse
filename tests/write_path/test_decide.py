"""Tests for #5 ``decide_path`` — the pure, LLM-free path decision (SO-5b).

``decide_path`` is a pure function of ``(payload, extraction_mode)``. In MVP-0
the ``extraction_mode`` flag and the ``source_type == 'importer'`` guard are
authoritative; size/density heuristics are advisory (MVP-1). The decision never
spends an LLM call (using an LLM to decide whether to use an LLM would be
paradoxical and non-deterministic).
"""

from __future__ import annotations

import pytest

from seahorse.facade.types import RememberPayload
from seahorse.write_path.decide import InvalidExtractionMode, decide_path


def _payload(by: dict | None = None) -> RememberPayload:
    return RememberPayload(body="body", by=by or {"source_type": "agent"})


class TestDecidePathFlag:
    def test_skip_flag_yields_skip(self) -> None:
        d = decide_path(_payload(), "skip")
        assert d.path == "skip"
        assert d.requested_mode == "skip"

    def test_llm_flag_yields_llm(self) -> None:
        d = decide_path(_payload(), "llm")
        assert d.path == "llm"
        assert d.requested_mode == "llm"


class TestDecidePathImporterGuard:
    def test_importer_forces_skip_even_with_llm_flag(self) -> None:
        # The importer guard is authoritative: importers carry F3.3-migrated
        # frontmatter, so they never need LLM extraction.
        d = decide_path(_payload({"source_type": "importer"}), "llm")
        assert d.path == "skip"
        assert d.requested_mode == "llm"
        assert d.reason == "importer_skip_guard"

    def test_importer_with_skip_flag(self) -> None:
        d = decide_path(_payload({"source_type": "importer"}), "skip")
        assert d.path == "skip"
        assert d.reason == "importer_skip_guard"

    def test_non_importer_source_does_not_trigger_guard(self) -> None:
        d = decide_path(_payload({"source_type": "human"}), "llm")
        assert d.path == "llm"
        assert d.reason != "importer_skip_guard"


class TestDecidePathValidation:
    def test_llm_partial_rejected(self) -> None:
        # Reserved modes (MVP-1) are refused loud, not silently dropped to skip.
        with pytest.raises(InvalidExtractionMode):
            decide_path(_payload(), "llm_partial")  # type: ignore[arg-type]

    def test_consolidated_rejected(self) -> None:
        with pytest.raises(InvalidExtractionMode):
            decide_path(_payload(), "consolidated")  # type: ignore[arg-type]

    def test_invalid_mode_is_value_error(self) -> None:
        assert issubclass(InvalidExtractionMode, ValueError)


class TestDecidePathPurity:
    def test_does_not_touch_engine_or_repo(self) -> None:
        # decide_path reads ONLY the payload + flag; no I/O, no engine, no repo.
        # Proven by signature: it takes no engine/repo argument at all.
        d = decide_path(_payload(), "skip")
        assert d.path == "skip"

    def test_same_inputs_same_output(self) -> None:
        p = _payload({"source_type": "agent"})
        assert decide_path(p, "skip") == decide_path(p, "skip")