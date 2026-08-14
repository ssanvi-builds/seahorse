"""Episode id generation (owned by the engine).

The default write path (``remember`` for ``agent``/``human``/``system``)
generates a UUIDv7 (RFC 9562 — timestamp-ordered, sortable, unique). The
importer path (``source_type == "importer"`` with ``importer_vendor`` set) uses
``deterministic_id`` (UUIDv5 over NAMESPACE_URL) so re-import yields the same
id, making the storage write idempotent.

Python < 3.14 has no ``uuid.uuid7``; we synthesize a RFC 9562 UUIDv7 from
``time.time_ns()`` + ``os.urandom`` and fall back to the stdlib when present.
UUIDv5 (version bit 5) and UUIDv7 (version bit 7) never collide structurally.
"""

from __future__ import annotations

import os
import time
import uuid

# 2^62 - 1 mask for the random_b field (62 bits).
_RAND_B_MASK = 0x3FFFFFFFFFFFFFFF
# 2^12 - 1 mask for the random_a field (12 bits).
_RAND_A_MASK = 0x0FFF
# RFC 4122 variant bits (binary 10) occupy the top 2 bits of byte 8.
_VARIANT = 0b10
_NS_PER_MS = 1_000_000


def new_uuid7() -> str:
    """Return a fresh UUIDv7 string (RFC 9562)."""
    if hasattr(uuid, "uuid7"):  # Python >= 3.14
        return str(uuid.uuid7())  # type: ignore[attr-defined]

    ts_ms = time.time_ns() // _NS_PER_MS  # 48-bit unix epoch milliseconds
    rand = os.urandom(10)
    rand_a = int.from_bytes(rand[:2], "big") & _RAND_A_MASK
    rand_b = int.from_bytes(rand[2:10], "big") & _RAND_B_MASK
    uuid_int = (
        (ts_ms << 80)
        | (0x7 << 76)  # version 7
        | (rand_a << 64)
        | (_VARIANT << 62)
        | rand_b
    )
    return str(uuid.UUID(int=uuid_int))


def deterministic_id(vendor: str, source_record_id: str, canonical_body_hash: str) -> str:
    """Return a deterministic UUIDv5 string for an imported note.

    Namespace: ``uuid.NAMESPACE_URL`` (fixed). Name:
    ``f"{vendor}:{source_record_id}:{canonical_body_hash}"``. Same inputs yield
    the same id, so re-import is idempotent at the storage layer.
    """
    name = f"{vendor}:{source_record_id}:{canonical_body_hash}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))