"""Validate new_uuid7 + deterministic_id (Phase 2, owned #2).

SO-4b: ``remember`` generates UUIDv7 by default (timestamp-ordered, sortable,
unique). Importer path with ``importer_vendor`` set uses ``deterministic_id``
(UUIDv5, RFC 4122 over NAMESPACE_URL) so re-import yields the same id. UUIDv5
(version bit 5) never collides with UUIDv7 (version bit 7).
"""

from __future__ import annotations

import uuid

from seahorse.engine.ids import deterministic_id, new_uuid7


def test_new_uuid7_returns_str():
    s = new_uuid7()
    assert isinstance(s, str)


def test_new_uuid7_is_version_7():
    parsed = uuid.UUID(new_uuid7())
    assert parsed.version == 7


def test_new_uuid7_unique_across_many():
    ids = {new_uuid7() for _ in range(1000)}
    assert len(ids) == 1000


def test_new_uuid7_timestamp_ordered():
    # RFC 9562: lexicographic sort follows creation order within the same ms
    # resolution is too coarse to guarantee strict ordering under rapid calls,
    # but the leading 48 bits are the timestamp so earlier ids sort first at
    # second granularity. Generate two with a guaranteed gap.
    import time

    a = new_uuid7()
    time.sleep(0.002)
    b = new_uuid7()
    assert a < b  # ms-granularity timestamp strictly greater after 2ms


def test_deterministic_id_is_version_5():
    parsed = uuid.UUID(deterministic_id("mem0", "rec-1", "deadbeef"))
    assert parsed.version == 5


def test_deterministic_id_deterministic():
    a = deterministic_id("mem0", "rec-1", "deadbeef")
    b = deterministic_id("mem0", "rec-1", "deadbeef")
    assert a == b


def test_deterministic_id_changes_with_vendor():
    a = deterministic_id("mem0", "rec-1", "deadbeef")
    b = deterministic_id("zep", "rec-1", "deadbeef")
    assert a != b


def test_deterministic_id_changes_with_record_or_hash():
    base = deterministic_id("mem0", "rec-1", "deadbeef")
    assert base != deterministic_id("mem0", "rec-2", "deadbeef")
    assert base != deterministic_id("mem0", "rec-1", "feedface")


def test_version_bits_never_collide_v5_vs_v7():
    # A UUIDv7 id and a UUIDv5 id are distinct types; structurally they differ
    # in the version nibble so equality is impossible.
    v7 = new_uuid7()
    v5 = deterministic_id("mem0", "rec-1", canonical_body_hash="x")
    assert v7 != v5
    assert uuid.UUID(v7).version != uuid.UUID(v5).version