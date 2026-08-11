"""Shared fixtures for procedural tests.

The procedural layer is a client of #12 (MemoryFacade) — tests use the facade
recording doubles (mirror of the facade conftest) so delegation invariants are
structurally enforced. ``make_episode`` builds a minimal ``Episode`` for the
trust/shaper tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seahorse.contracts.engine import Episode


def make_episode(
    ep_id: str = "e1",
    *,
    body: str = "body",
    subject: str | None = "skill",
    fact_id: str | None = "fact-1",
    cognitive_type: str | None = "procedural",
    source_type: str | None = "agent",
    provenance: dict[str, Any] | None = None,
    supersedes: str | None = None,
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="1.1",
        provenance=provenance if provenance is not None else {"source_type": source_type},
        body=body,
        subject=subject,
        fact_id=fact_id,
        valid_at=None,
        invalid_at=None,
        expired_at=None,
        supersedes=supersedes,
        cognitive_type=cognitive_type,
        source_type=source_type,
        title="Skill",
    )


@pytest.fixture()
def write_path():
    from tests.facade.conftest import RecordingWritePath

    return RecordingWritePath()


@pytest.fixture()
def facade(write_path):
    """A facade over recording doubles (mirror of the facade conftest)."""
    from seahorse.facade.facade import MemoryFacade
    from seahorse.facade.types import FacadeConfig
    from seahorse.facade.vigente_retriever import VigenteListingRetriever
    from tests.facade.conftest import RecordingEngine, RecordingShaper

    eng = RecordingEngine()
    shaper = RecordingShaper()
    clk = lambda: datetime(2026, 7, 16, tzinfo=UTC)  # noqa: E731
    ret = VigenteListingRetriever(engine=eng, clock=clk, config=FacadeConfig())
    f = MemoryFacade(
        engine=eng,
        write_path=write_path,
        shaper=shaper,
        retriever=ret,
        clock=clk,
        config=FacadeConfig(),
    )
    return f


__all__ = ["make_episode"]
