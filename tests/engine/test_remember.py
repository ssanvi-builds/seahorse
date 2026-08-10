"""Validate BiTemporalEngine.remember (Phase 6, owned #2).

SO-4b: the importer path (``source_type == "importer"`` with ``importer_vendor``
set) generates a deterministic UUIDv5 from (vendor, source_record_id,
canonical_body_hash) so re-import is idempotent at the storage layer; every other
source generates a UUIDv7. Idempotency is check-then-skip: if the derived id
already exists with the same canonical body hash, ``remember`` is a NOOP.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.engine.canonical import canonical_body_hash
from seahorse.engine.engine import BiTemporalEngine
from seahorse.engine.ids import deterministic_id

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=3)

_BODY = "# Sergio uses Python\nfor data work.\n"
_IMPORTER_BY = {
    "source_type": "importer",
    "importer_vendor": "mem0",
    "source_record_id": "rec-42",
    "agent_id": "mem0-import",
}


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit), repo, audit


# --- id generation by source (SO-4b) ----------------------------------------


def test_remember_default_generates_uuidv7(engine):
    eng, repo, audit = engine
    import uuid

    wr = eng.remember(body=_BODY, by={"source_type": "agent", "agent_id": "a1"}, now=NOW)
    assert wr.status == "ACTIVE"
    assert uuid.UUID(wr.ep_id).version == 7
    assert repo.get(wr.ep_id) is not None


def test_remember_importer_uses_deterministic_uuidv5(engine):
    eng, repo, audit = engine
    import uuid

    wr = eng.remember(body=_BODY, by=_IMPORTER_BY, now=NOW)
    expected = deterministic_id("mem0", "rec-42", canonical_body_hash(_BODY))
    assert wr.ep_id == expected
    assert uuid.UUID(wr.ep_id).version == 5


def test_remember_agent_without_vendor_uses_uuidv7(engine):
    eng, repo, audit = engine
    import uuid

    # source_type=importer but no importer_vendor -> falls back to UUIDv7.
    wr = eng.remember(
        body=_BODY, by={"source_type": "importer", "agent_id": "x"}, now=NOW
    )
    assert uuid.UUID(wr.ep_id).version == 7


def test_remember_two_uuidv7_are_distinct(engine):
    eng, repo, audit = engine
    a = eng.remember(body="# Topic A\n", by={"source_type": "agent"}, now=NOW)
    b = eng.remember(body="# Topic B\n", by={"source_type": "agent"}, now=NOW)
    assert a.ep_id != b.ep_id


def test_remember_importer_without_source_record_id_is_deterministic_and_idempotent(engine):
    # The `or ""` fallback for a missing source_record_id is a specific code
    # choice: two imports of the same body with the same vendor but no
    # source_record_id yield the same UUIDv5 and the re-import is a NOOP.
    eng, repo, audit = engine
    import uuid

    by = {"source_type": "importer", "importer_vendor": "mem0", "agent_id": "mem0-import"}
    first = eng.remember(body=_BODY, by=by, now=NOW)
    assert first.status == "ACTIVE"
    assert uuid.UUID(first.ep_id).version == 5
    second = eng.remember(body=_BODY, by=by, now=NOW)
    assert second.status == "NOOP"
    assert second.ep_id == first.ep_id


# --- idempotency (check-then-skip) -------------------------------------------


def test_remember_importer_reimport_is_noop(engine):
    eng, repo, audit = engine
    first = eng.remember(body=_BODY, by=_IMPORTER_BY, now=NOW)
    assert first.status == "ACTIVE"
    second = eng.remember(body=_BODY, by=_IMPORTER_BY, now=NOW)
    assert second.status == "NOOP"
    assert second.ep_id == first.ep_id
    assert second.fact_id == first.fact_id
    # storage unchanged: still one episode for that id.
    assert repo.get(first.ep_id) is not None


def test_remember_importer_reimport_no_extra_audit_or_row(engine):
    eng, repo, audit = engine
    eng.remember(body=_BODY, by=_IMPORTER_BY, now=NOW)
    eng.remember(body=_BODY, by=_IMPORTER_BY, now=NOW)  # NOOP
    events = audit.query()
    assert len(events) == 1  # only the first apply emitted an audit event


# --- delegation to apply_fact ------------------------------------------------


def test_remember_pending_when_valid_at_future(engine):
    eng, repo, audit = engine
    wr = eng.remember(
        body="# Future\n", by={"source_type": "human"}, valid_at=FUTURE, now=NOW
    )
    assert wr.status == "PENDING_INGEST"


def test_remember_persists_summary(engine):
    # OQ3 enabler: engine.remember persists the editorial summary verbatim.
    eng, repo, _audit = engine
    result = eng.remember(
        body="# Title\n\nContent.",
        by={"source_type": "agent"},
        summary="A caller summary",
    )
    ep = repo.get(result.ep_id)
    assert ep.summary == "A caller summary"


def test_remember_summary_none_persists_none(engine):
    eng, repo, _audit = engine
    result = eng.remember(
        body="# Title\n\nContent.",
        by={"source_type": "agent"},
        summary=None,
    )
    ep = repo.get(result.ep_id)
    assert ep.summary is None


def test_remember_agent_custom_valid_at_rejected_by_guard(engine):
    from seahorse.engine import errors

    eng, repo, audit = engine
    with pytest.raises(errors.EngineError) as exc:
        eng.remember(
            body="# X\n", by={"source_type": "agent"}, valid_at=NOW - timedelta(days=1), now=NOW
        )
    assert exc.value.code == errors.E_VALID_AT_HUMAN_ONLY

# --- supersedes / supersedes_reason (Sprint B distill enabler) ---------------


def test_remember_accepts_supersedes_and_reason(engine):
    """The distill primitive writes a consolidated episode that references its
    representative source via ``supersedes`` (obsiforge §5.2) WITHOUT
    invalidating it — the sources stay vigente (they are the evidence)."""
    eng, repo, audit = engine
    source = eng.remember(body=_BODY, by={"source_type": "agent", "agent_id": "a1"}, now=NOW)
    wr = eng.remember(
        body="# Sergio uses Python\nfor data work.\n",
        by={"source_type": "system", "agent_id": "consolidator"},
        cognitive_type="semantic",
        subject="sergio",
        supersedes=source.ep_id,
        supersedes_reason="merge",
        now=NOW,
    )
    assert wr.status == "ACTIVE"
    ep = repo.get(wr.ep_id)
    assert ep.supersedes == source.ep_id
    assert ep.supersedes_reason == "merge"
    # The source stays vigente (not invalidated by the consolidation).
    src = repo.get(source.ep_id)
    assert src.invalid_at is None


def test_remember_supersedes_defaults_none(engine):
    eng, repo, audit = engine
    wr = eng.remember(body=_BODY, by={"source_type": "agent", "agent_id": "a1"}, now=NOW)
    ep = repo.get(wr.ep_id)
    assert ep.supersedes is None
    assert ep.supersedes_reason is None
