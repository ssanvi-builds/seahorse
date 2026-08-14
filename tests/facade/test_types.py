"""Tests for the primitives facade payload types.

``Provenance`` is a ``TypedDict(total=False)`` — at runtime it IS a plain dict,
so it passes straight into the engine's ``by: dict`` parameter and is
JSON-serializable for the MCP server. ``RememberPayload``/``RecallPayload``/
``FacadeConfig`` are frozen dataclasses. ``COGNITIVE_TYPES``/``SOURCE_TYPES``
are informative frozensets (referenced by the MCP server and CLI; NOT enforced
by the primitives facade in the first release — the engine and its schema are
the authority, so the primitives facade does not replicate domain invariants).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import get_type_hints

import pytest

from seahorse.contracts.index import PITKind
from seahorse.disclosure.types import TOP_K, PITPoint
from seahorse.facade.types import (
    COGNITIVE_TYPES,
    SOURCE_TYPES,
    FacadeConfig,
    Provenance,
    RecallPayload,
    RememberPayload,
)


class TestProvenance:
    def test_is_a_plain_dict_at_runtime(self) -> None:
        # TypedDict(total=False) collapses to dict — passes straight to engine.remember(by=...).
        p: Provenance = {"source_type": "agent", "agent_id": "a1"}
        assert isinstance(p, dict)
        assert p["source_type"] == "agent"

    def test_all_keys_optional(self) -> None:
        # total=False: no key is required.
        p: Provenance = {}
        assert p == {}

    def test_json_serializable(self) -> None:
        import json

        p: Provenance = {
            "source_type": "agent",
            "agent_id": "a1",
            "model_used": None,
            "confidence": 1.0,
        }
        assert json.loads(json.dumps(p)) == p


class TestCognitiveAndSourceTypes:
    def test_cognitive_types_includes_active_and_reserved(self) -> None:
        active = {"episodic", "semantic", "social", "project_doc"}
        reserved = {"procedural", "working"}
        assert active <= COGNITIVE_TYPES
        assert reserved <= COGNITIVE_TYPES

    def test_source_types_canonical_four(self) -> None:
        assert frozenset({"agent", "human", "importer", "system"}) == SOURCE_TYPES

    def test_frozensets_are_immutable(self) -> None:
        assert isinstance(COGNITIVE_TYPES, frozenset)
        assert isinstance(SOURCE_TYPES, frozenset)


class TestRememberPayload:
    def test_required_body_and_by(self) -> None:
        p = RememberPayload(body="hello", by={"source_type": "agent"})
        assert p.body == "hello"
        assert p.by == {"source_type": "agent"}

    def test_defaults(self) -> None:
        p = RememberPayload(body="hello", by={"source_type": "agent"})
        assert p.valid_at is None
        assert p.cognitive_type is None
        assert p.title is None
        assert p.tags == ()
        assert p.schema_version == "1.1"

    def test_frozen(self) -> None:
        p = RememberPayload(body="hello", by={"source_type": "agent"})
        with pytest.raises(FrozenInstanceError):
            p.body = "x"  # type: ignore[misc]

    def test_tags_default_empty_tuple(self) -> None:
        p = RememberPayload(body="hello", by={"source_type": "agent"})
        # forward-compat field; the first release rejects non-empty at the
        # facade border
        assert p.tags == ()


class TestRecallPayload:
    def test_required_query(self) -> None:
        p = RecallPayload(query="sergio")
        assert p.query == "sergio"

    def test_defaults(self) -> None:
        p = RecallPayload(query="sergio")
        assert p.pit is None
        assert p.k == TOP_K
        assert p.cognitive_type is None
        assert p.subject_filter is None
        assert p.anchor_ep_id is None
        assert p.hops == 1

    def test_frozen(self) -> None:
        p = RecallPayload(query="sergio")
        with pytest.raises(FrozenInstanceError):
            p.query = "x"  # type: ignore[misc]

    def test_pit_point_accepted(self) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        p = RecallPayload(query="sergio", pit=PITPoint(kind="state_at", t=t))
        assert p.pit is not None
        assert p.pit.kind == "state_at"


class TestFacadeConfig:
    def test_defaults_are_mvp0(self) -> None:
        c = FacadeConfig()
        assert c.default_extraction_mode == "skip"
        assert c.default_cognitive_type is None
        assert c.top_k == TOP_K
        assert c.phase == "mvp0"

    def test_frozen(self) -> None:
        c = FacadeConfig()
        with pytest.raises(FrozenInstanceError):
            c.phase = "mvp1"  # type: ignore[misc]


class TestImportsAreStable:
    def test_pitpoint_reexported_from_disclosure(self) -> None:
        # The primitives facade does not redefine PITPoint — it imports the
        # disclosure shaper's carrier.
        from seahorse.facade import types as facade_types

        assert facade_types.PITPoint is PITPoint

    def test_pitkind_is_the_literal_from_contracts(self) -> None:
        hints = get_type_hints(PITPoint)
        assert hints["kind"] is PITKind or str(hints["kind"]) == str(PITKind)