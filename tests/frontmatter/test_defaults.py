"""Migration defaults unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.frontmatter.defaults import (
    MIGRATOR_AGENT_ID,
    SCHEMA_VERSION_MVP0,
    is_suspicious_mtime,
    migration_defaults,
)

NOW = datetime(2026, 7, 22, 10, 0, 0, tzinfo=UTC)
GOOD_MTIME = datetime(2026, 7, 15, 8, 30, 0, tzinfo=UTC)


class TestMigrationDefaults:
    def test_returns_episode_and_empty_collisions_for_good_mtime(self) -> None:
        ep, collisions = migration_defaults(GOOD_MTIME, "sess-1", now=NOW)
        assert collisions == []
        assert ep.schema_version == SCHEMA_VERSION_MVP0
        assert ep.cognitive_type == "semantic"

    def test_valid_at_equals_created_at_equals_file_mtime(self) -> None:
        ep, _ = migration_defaults(GOOD_MTIME, "sess-1", now=NOW)
        assert ep.valid_at == ep.created_at == GOOD_MTIME

    def test_provenance_carries_migrator_fields(self) -> None:
        ep, _ = migration_defaults(GOOD_MTIME, "sess-1", now=NOW)
        assert ep.provenance["agent_id"] == MIGRATOR_AGENT_ID
        assert ep.provenance["session_id"] == "sess-1"
        assert ep.provenance["source_type"] == "human"
        assert ep.provenance["extraction_mode"] == "skip"

    def test_body_is_none(self) -> None:
        # The migrator writes the file's body verbatim, not the episode's body.
        ep, _ = migration_defaults(GOOD_MTIME, "sess-1", now=NOW)
        assert ep.body is None

    def test_id_is_a_uuidv7_string(self) -> None:
        ep, _ = migration_defaults(GOOD_MTIME, "sess-1", now=NOW)
        # UUIDv7 has the version nibble 7 at the version position.
        assert ep.id[14] == "7"

    def test_naive_mtime_falls_back_to_now(self) -> None:
        naive = datetime(2026, 7, 15, 8, 30, 0)  # no tzinfo
        ep, collisions = migration_defaults(naive, "sess-1", now=NOW)
        assert collisions == ["timestamp fallback: suspicious file mtime"]
        assert ep.created_at == NOW
        assert ep.valid_at == NOW

    def test_future_mtime_falls_back_to_now(self) -> None:
        future = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)  # > 24h ahead of NOW
        ep, collisions = migration_defaults(future, "sess-1", now=NOW)
        assert collisions == ["timestamp fallback: suspicious file mtime"]
        assert ep.created_at == NOW

    def test_pre_2000_mtime_falls_back_to_now(self) -> None:
        epoch0 = datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC)
        ep, collisions = migration_defaults(epoch0, "sess-1", now=NOW)
        assert collisions == ["timestamp fallback: suspicious file mtime"]
        assert ep.created_at == NOW


class TestIsSuspiciousMtime:
    def test_good_mtime_is_not_suspicious(self) -> None:
        assert is_suspicious_mtime(GOOD_MTIME, now=NOW) is False

    def test_naive_is_suspicious(self) -> None:
        assert is_suspicious_mtime(datetime(2026, 7, 15), now=NOW) is True

    def test_far_future_is_suspicious(self) -> None:
        assert is_suspicious_mtime(datetime(2027, 1, 1, tzinfo=UTC), now=NOW) is True

    def test_pre_2000_is_suspicious(self) -> None:
        assert is_suspicious_mtime(datetime(1970, 1, 1, tzinfo=UTC), now=NOW) is True

    def test_near_future_within_margin_is_not_suspicious(self) -> None:
        # 1 hour ahead — within the 24h margin, not suspicious.
        soon = NOW.replace(hour=NOW.hour + 1) if NOW.hour < 23 else NOW
        assert is_suspicious_mtime(soon, now=NOW) is False