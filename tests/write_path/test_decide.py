"""Tests for the write path's ``decide_path`` — the pure, LLM-free path decision.

``decide_path`` is a pure function of ``(payload, extraction_mode)``. In the
first release the ``extraction_mode`` flag and the ``source_type == 'importer'``
guard are authoritative; size/density heuristics are advisory (a later release).
The decision never spends an LLM call (using an LLM to decide whether to use an
LLM would be paradoxical and non-reproducible).

The write path extends the source-type guard to human/system (all non-agent
source_types force skip) and wires the advisory heuristics (logged only, no
override) for the agent+llm case.
"""

from __future__ import annotations

import logging

import pytest

from seahorse.facade.types import RememberPayload
from seahorse.write_path.decide import InvalidExtractionMode, decide_path


def _payload(by: dict | None = None, body: str = "body") -> RememberPayload:
    return RememberPayload(body=body, by=by or {"source_type": "agent"})


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
        # The importer guard is authoritative: importers carry migrated
        # frontmatter, so they never need LLM extraction.
        d = decide_path(_payload({"source_type": "importer"}), "llm")
        assert d.path == "skip"
        assert d.requested_mode == "llm"
        assert d.reason == "importer_skip_guard"

    def test_importer_with_skip_flag(self) -> None:
        d = decide_path(_payload({"source_type": "importer"}), "skip")
        assert d.path == "skip"
        assert d.reason == "importer_skip_guard"


class TestDecidePathSourceTypeGuard:
    """All non-agent source_types force skip. human/importer/system never spend
    an LLM call."""

    def test_human_with_llm_flag_forces_skip(self) -> None:
        d = decide_path(_payload({"source_type": "human"}), "llm")
        assert d.path == "skip"
        assert d.requested_mode == "llm"
        assert d.reason == "human_skip_guard"

    def test_human_with_skip_flag_forces_skip(self) -> None:
        d = decide_path(_payload({"source_type": "human"}), "skip")
        assert d.path == "skip"
        assert d.requested_mode == "skip"
        assert d.reason == "human_skip_guard"

    def test_system_with_llm_flag_forces_skip(self) -> None:
        d = decide_path(_payload({"source_type": "system"}), "llm")
        assert d.path == "skip"
        assert d.requested_mode == "llm"
        assert d.reason == "system_skip_guard"

    def test_system_with_skip_flag_forces_skip(self) -> None:
        d = decide_path(_payload({"source_type": "system"}), "skip")
        assert d.path == "skip"
        assert d.requested_mode == "skip"
        assert d.reason == "system_skip_guard"

    def test_agent_with_llm_flag_not_guarded(self) -> None:
        # agent is the only source_type that may take the llm path.
        d = decide_path(_payload({"source_type": "agent"}), "llm")
        assert d.path == "llm"
        assert d.requested_mode == "llm"
        assert d.reason == "flag_llm"

    def test_unknown_source_type_forces_skip(self) -> None:
        # Defense-in-depth: an out-of-vocabulary source_type never reaches the
        # llm path (the facade is the primary enforcer; this is the backstop).
        d = decide_path(_payload({"source_type": "widget"}), "llm")
        assert d.path == "skip"
        assert d.requested_mode == "llm"
        assert d.reason == "non_agent_skip_guard"

    def test_missing_source_type_forces_skip(self) -> None:
        # by={} (no source_type key at all) — use RememberPayload directly
        # because the _payload helper collapses {} to the agent default.
        d = decide_path(RememberPayload(body="body", by={}), "llm")
        assert d.path == "skip"
        assert d.requested_mode == "llm"
        assert d.reason == "non_agent_skip_guard"

    def test_none_source_type_forces_skip(self) -> None:
        d = decide_path(_payload({"source_type": None}), "llm")
        assert d.path == "skip"
        assert d.requested_mode == "llm"
        assert d.reason == "non_agent_skip_guard"


class TestDecidePathValidation:
    def test_llm_partial_rejected(self) -> None:
        # ``llm_partial`` is fully reserved (not schema-valid): refused loud,
        # not silently dropped to skip.
        with pytest.raises(InvalidExtractionMode):
            decide_path(_payload(), "llm_partial")  # type: ignore[arg-type]

    def test_consolidated_rejected(self) -> None:
        # ``consolidated`` IS schema-valid and round-trippable (batch-distillation
        # marker), but NOT routable by the single-episode write path — the batch
        # distillation writes via ``engine.remember`` directly, bypassing
        # ``decide_path``. Refusing loud keeps the
        # write path honest: a single-episode ingest can never honor it.
        with pytest.raises(InvalidExtractionMode):
            decide_path(_payload(), "consolidated")

    def test_invalid_mode_is_value_error(self) -> None:
        assert issubclass(InvalidExtractionMode, ValueError)


class TestDecidePathAdvisoryHeuristics:
    """Size/density heuristics are advisory — logged only, they NEVER override
    the flag. They fire only when ``source_type == 'agent'`` AND
    ``mode == 'llm'`` (the only case that reaches the heuristics; non-agent
    guards short-circuit first)."""

    def test_size_over_5kb_with_llm_warns_but_keeps_llm(self, caplog) -> None:
        body = "This is a sentence. " * 300  # ~5.7KB of prose (not dense)
        assert len(body) > 5120
        with caplog.at_level(logging.WARNING, logger="seahorse.write_path.decide"):
            d = decide_path(_payload({"source_type": "agent"}, body=body), "llm")
        assert d.path == "llm"  # advisory does NOT override the flag
        assert any("5KB" in r.message or "cost" in r.message for r in caplog.records)

    def test_dense_content_with_llm_warns_but_keeps_llm(self, caplog) -> None:
        body = "x = 1\ny = 2\nz = 3\nfoo = bar\n"  # small but technical/dense
        with caplog.at_level(logging.WARNING, logger="seahorse.write_path.decide"):
            d = decide_path(_payload({"source_type": "agent"}, body=body), "llm")
        assert d.path == "llm"  # advisory does NOT override the flag
        assert any("dense" in r.message or "skip recommended" in r.message for r in caplog.records)

    def test_large_and_dense_emits_both_warnings(self, caplog) -> None:
        # The two heuristics are independent: a body that is BOTH >5KB and
        # dense must emit BOTH warnings (no elif/early-return masks the second).
        body = "x = 1\ny = 2\nz = 3\nfoo = bar\n" * 200  # ~5.6KB and dense
        assert len(body) > 5120
        with caplog.at_level(logging.WARNING, logger="seahorse.write_path.decide"):
            d = decide_path(_payload({"source_type": "agent"}, body=body), "llm")
        assert d.path == "llm"
        messages = [r.message for r in caplog.records]
        assert any("5KB" in m or "cost" in m for m in messages), messages
        assert any("dense" in m for m in messages), messages

    def test_skip_mode_emits_no_size_warning(self, caplog) -> None:
        body = "This is a sentence. " * 300
        with caplog.at_level(logging.WARNING, logger="seahorse.write_path.decide"):
            decide_path(_payload({"source_type": "agent"}, body=body), "skip")
        assert not [r for r in caplog.records if "5KB" in r.message or "cost" in r.message]

    def test_human_source_emits_no_heuristic_warning(self, caplog) -> None:
        # The human guard short-circuits before the heuristics run.
        body = "x = 1\ny = 2\nz = 3\nfoo = bar\n" * 200  # large + dense
        with caplog.at_level(logging.WARNING, logger="seahorse.write_path.decide"):
            d = decide_path(_payload({"source_type": "human"}, body=body), "llm")
        assert d.path == "skip"
        assert not [r for r in caplog.records if "5KB" in r.message or "dense" in r.message]

    def test_small_prose_llm_emits_no_warning(self, caplog) -> None:
        body = "This is a small prose note.\nAnother line here.\n"
        with caplog.at_level(logging.WARNING, logger="seahorse.write_path.decide"):
            d = decide_path(_payload({"source_type": "agent"}, body=body), "llm")
        assert d.path == "llm"
        assert not caplog.records


class TestDecidePathPurity:
    def test_does_not_touch_engine_or_repo(self) -> None:
        # decide_path reads ONLY the payload + flag; no I/O, no engine, no repo.
        # Proven by signature: it takes no engine/repo argument at all.
        d = decide_path(_payload(), "skip")
        assert d.path == "skip"

    def test_same_inputs_same_output(self) -> None:
        p = _payload({"source_type": "agent"})
        assert decide_path(p, "skip") == decide_path(p, "skip")