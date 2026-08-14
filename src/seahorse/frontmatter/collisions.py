"""Frontmatter key collisions.

Two collision classes the migrator must report (never auto-resolve):

1. **Legacy↔on-disk name collision.** An Obsidian/legacy frontmatter key names a
   concept that the on-disk format also names, with a different semantic. The
   classic case: Obsidian ``created`` (a free-form date the human sets) vs the
   format's ``created_at`` (the bi-temporal created_at, UTC datetime). The
   migrator preserves the legacy key intact and ADDS the canonical key alongside,
   recording the collision so the human can reconcile later. ``COLLISION_MAP``
   lists the pairs we know about.

2. **x-* reserved collision.** The on-disk format reserves the ``x-`` prefix for
   importer/plugin metadata. A legacy key that ALREADY uses an ``x-`` name
   reserved for a core field (e.g. ``x-valid-at``) is incompatible: the
   validator would reject the merged frontmatter, so the migrator refuses to
   migrate and logs to ``migration_errors.log``.

Part of the frontmatter migrator, stdlib-only (no ruamel, no pydantic).
"""

from __future__ import annotations

# Legacy Obsidian key -> the on-disk key it semantically collides with. The legacy
# key is PRESERVED (case B); the canonical key is ADDED; the pair is logged.
COLLISION_MAP: dict[str, str] = {
    "created": "created_at",
    "modified": "modified_at",  # not an on-disk core field, but reserved name space
    "publish": "publish_state",  # informational; recorded for human awareness
}

# x-* names reserved for on-disk core fields. A legacy key using one of these is
# incompatible (validator would reject the merged frontmatter).
X_RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "x-valid-at",
        "x-invalid-at",
        "x-expired-at",
        "x-supersedes",
        "x-supersedes-reason",
        "x-schema-version",
        "x-seahorse-id",
    }
)


def detect_legacy_collisions(legacy_keys: set[str]) -> list[str]:
    """Return the collision pairs present, as ``"legacy vs f31"`` strings.

    Only keys that are actually present AND have an on-disk counterpart in
    ``COLLISION_MAP`` are reported. Order is stable (sorted by legacy key) so
    the manifest/diff is deterministic.
    """
    pairs = [
        f"{legacy} vs {f31}"
        for legacy, f31 in COLLISION_MAP.items()
        if legacy in legacy_keys
    ]
    return sorted(pairs)


def detect_x_reserved_collision(keys: set[str]) -> str | None:
    """Return the offending x-* reserved key if any legacy key claims one.

    ``None`` when there is no collision. A non-``None`` result means the note is
    case D (incompatible) — the migrator refuses to write and logs an error.
    """
    for key in keys:
        if key in X_RESERVED_KEYS:
            return key
    return None