"""Shared fixtures for the frontmatter adapter/migrator tests (#3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.contracts.episode import Episode

# A valid UUIDv7 used across the frontmatter tests (version nibble 7, variant 8).
UUIDV7 = "01234567-89ab-7def-8123-456789abcdef"
CREATED = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def make_episode(
    *,
    id: str = UUIDV7,
    created_at: datetime = CREATED,
    schema_version: str = "0.1.0",
    body: str = "# Madrid\nSergio lives in Madrid.\n",
    title: str | None = "Madrid",
    tags: list[str] | None = None,
    cognitive_type: str | None = "semantic",
    invalid_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
    supersedes_reason: str | None = None,
    provenance: dict | None = None,
    valid_at: datetime | None = None,
) -> Episode:
    """A valid F3.1 episode for frontmatter round-trip tests."""
    return Episode(
        id=id,
        created_at=created_at,
        schema_version=schema_version,
        provenance=provenance
        or {
            "agent_id": "seahorse/migrator",
            "session_id": "s1",
            "source_type": "human",
            "extraction_mode": "skip",
        },
        body=body,
        title=title,
        tags=tags if tags is not None else ["geo", "person"],
        cognitive_type=cognitive_type,
        invalid_at=invalid_at,
        expired_at=expired_at,
        supersedes=supersedes,
        supersedes_reason=supersedes_reason,
        valid_at=valid_at,
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A throwaway vault directory."""
    return tmp_path