"""``x-seahorse-derived-from`` on materialized consolidated notes (F3.2).

The materializer emits the structured cluster membership the distillation
records in provenance (``derived_from`` edges): one ``{id, edge_kind}`` edge
per source episode. Emission is provenance-driven, so a legacy consolidated
episode (pre-1.4.0, no field) materializes without it, and the C1 invalidation
merge preserves a human-adjacent field byte for byte.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import parse_file
from seahorse.frontmatter.materialize import Materializer
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

_EDGES = [
    {"id": "0198f0a0-0000-7000-8000-000000000001", "edge_kind": "evidence"},
    {"id": "0198f0a0-0000-7000-8000-000000000002", "edge_kind": "evidence"},
    {"id": "0198f0a0-0000-7000-8000-000000000003", "edge_kind": "supersedes"},
]


@pytest.fixture()
def sidecar(tmp_path: Path):
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteSidecarIndexRepository(mgr)
    yield repo
    mgr.close()


def _episode(
    ep_id: str = "e1",
    *,
    subject: str = "my-subject",
    provenance: dict | None = None,
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=_NOW,
        schema_version="1.1",
        provenance=provenance or {"extraction_mode": "consolidated", "derived_from": _EDGES},
        body="# my-subject\n\nConsolidated body.",
        title="my-subject",
        subject=subject,
        valid_at=_NOW,
        cognitive_type="semantic",
        source_type="system",
    )


def test_consolidated_note_carries_derived_from(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar)
    r = m.materialize(_episode())
    assert r.status == "written"
    note = tmp_path / "Memory" / "my-subject.md"
    cm, body, _ = parse_file(note)
    assert cm.get("x-seahorse-derived-from") == _EDGES
    assert body == "# my-subject\n\nConsolidated body."


def test_legacy_consolidated_note_emits_no_field(tmp_path, sidecar) -> None:
    """A pre-1.4.0 consolidated episode carries no ``derived_from``; the
    materializer never invents membership."""
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar)
    m.materialize(_episode(provenance={"extraction_mode": "consolidated"}))
    cm, _body, _ = parse_file(tmp_path / "Memory" / "my-subject.md")
    assert "x-seahorse-derived-from" not in cm


def test_non_consolidated_episode_emits_no_field(tmp_path, sidecar) -> None:
    """Only consolidated episodes project their membership (an ``evidence``
    edge list behind an episodic note would be a category error)."""
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode="all")
    m.materialize(
        _episode(
            provenance={
                "source_type": "agent",
                "extraction_mode": "skip",
                "derived_from": _EDGES,
            },
        )
    )
    cm, _body, _ = parse_file(tmp_path / "Memory" / "my-subject.md")
    assert "x-seahorse-derived-from" not in cm


def test_rematerialize_is_idempotent(tmp_path, sidecar) -> None:
    """A backfill over an already-materialized episode skips (the note is
    byte-stable; the field is re-derived from provenance, never appended)."""
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar)
    ep = _episode()
    assert m.materialize(ep).status == "written"
    note = tmp_path / "Memory" / "my-subject.md"
    first = note.read_text()
    assert m.materialize(ep).reason == "already_materialized"
    assert note.read_text() == first


def test_invalidate_merge_preserves_the_field(tmp_path, sidecar) -> None:
    """The C1 invalidation merge (forget on the same episode id) rewrites the
    frontmatter from the episode's provenance — which still carries
    ``derived_from`` — so the field survives byte for byte."""
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar)
    m.materialize(_episode())
    note = tmp_path / "Memory" / "my-subject.md"
    first = note.read_text()

    invalidated = _episode().model_copy(
        update={"invalid_at": datetime(2026, 9, 16, 13, 0, tzinfo=UTC)}
    )
    r = m.invalidate(invalidated)
    assert r.status == "invalidated"
    cm, _body, parsed = parse_file(note)
    assert cm.get("x-seahorse-derived-from") == _EDGES
    assert parsed.invalid_at is not None
    assert "invalid_at" in first or "invalid_at" in note.read_text()


def test_unknown_edge_kinds_survive_the_round_trip(tmp_path, sidecar) -> None:
    """The enumeration freezes at format promotion; unknown ``edge_kind``
    values are preserved, never rejected (the same policy as every ``x-*``)."""
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar)
    edges = [{"id": "ep-x", "edge_kind": "unknown_new_kind"}]
    m.materialize(
        _episode(
            ep_id="e9",
            provenance={"extraction_mode": "consolidated", "derived_from": edges},
        )
    )
    cm, _body, _ = parse_file(tmp_path / "Memory" / "my-subject.md")
    assert cm.get("x-seahorse-derived-from") == edges
