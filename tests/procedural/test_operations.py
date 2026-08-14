"""Tests for ``record_procedure`` — deterministic skill creation.

The procedural layer is a client of the primitives facade (MemoryFacade):
``record_procedure`` delegates to ``facade.remember`` with
``cognitive_type=procedural`` and ``extraction_mode=skip`` (skip-first,
near-zero cost). The canonical body is validated BEFORE the facade call
(fail-loud, no partial write).
"""

from __future__ import annotations

import pytest

from seahorse.facade.errors import E_MISSING_SOURCE_TYPE, SeahorseError
from seahorse.procedural.operations import ProceduralError, record_procedure

CANONICAL = """## Trigger

When the user asks how to do X.

## Steps

1. Do A.
2. Do B.

## Validation

Check that C holds.

## Rationale

Because D.
"""


def _by(**overrides):
    by = {
        "source_type": "agent",
        "agent_id": "seahorse/test",
        "session_id": "s1",
    }
    by.update(overrides)
    return by


class TestRecordProcedure:
    def test_delegates_to_facade_remember_skip(self, facade, write_path):
        result = record_procedure(
            facade,
            body=CANONICAL,
            by=_by(),
            title="How to do X",
        )
        assert result.ep_id == "e1"
        assert len(write_path.ingest_calls) == 1
        call = write_path.ingest_calls[0]
        assert call["extraction_mode"] == "skip"
        assert call["payload"].cognitive_type == "procedural"
        assert call["payload"].body == CANONICAL
        assert call["payload"].title == "How to do X"

    def test_rejects_non_canonical_body(self, facade, write_path):
        with pytest.raises(ProceduralError) as exc:
            record_procedure(facade, body="## Trigger\n\nOnly trigger.", by=_by())
        assert "Steps" in str(exc.value)
        # No write reached the facade (fail-loud before any call).
        assert write_path.ingest_calls == []

    def test_rejects_empty_body(self, facade, write_path):
        with pytest.raises(ProceduralError):
            record_procedure(facade, body="   ", by=_by())
        assert write_path.ingest_calls == []

    def test_stores_x_metadata_in_provenance(self, facade, write_path):
        record_procedure(
            facade,
            body=CANONICAL,
            by=_by(),
            trigger="user asks how to do X",
            scope="personal",
            version="1.0",
        )
        call = write_path.ingest_calls[0]
        prov = call["payload"].by
        assert prov["x-seahorse-skill-trigger"] == "user asks how to do X"
        assert prov["x-seahorse-skill-scope"] == "personal"
        assert prov["x-seahorse-skill-version"] == "1.0"

    def test_omits_x_metadata_when_absent(self, facade, write_path):
        record_procedure(facade, body=CANONICAL, by=_by())
        prov = write_path.ingest_calls[0]["payload"].by
        assert "x-seahorse-skill-trigger" not in prov
        assert "x-seahorse-skill-version" not in prov

    def test_requires_source_type(self, facade, write_path):
        # The facade owns the source_type guard (E_MISSING_SOURCE_TYPE); the
        # procedural layer does not replicate it (delegation purity).
        with pytest.raises(SeahorseError) as excinfo:
            record_procedure(facade, body=CANONICAL, by=_by(source_type=None))
        assert excinfo.value.code == E_MISSING_SOURCE_TYPE
