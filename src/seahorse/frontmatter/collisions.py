"""Frontmatter key collisions (f5-03 §3.1/§3.3/§3.6).

Two collision classes the migrator must report (never auto-resolve, f5-03
§3.8):

1. **Legacy↔F3.1 name collision.** An Obsidian/legacy frontmatter key names a
   concept that F3.1 also names, with a different semantic. The classic case:
   Obsidian ``created`` (a free-form date the human sets) vs F3.1 ``created_at``
   (the bi-temporal created_at, UTC datetime). The migrator preserves the legacy
   key intact and ADDS the F3.1 key alongside, recording the collision so the
   human can reconcile later. ``COLLISION_MAP`` lists the pairs we know about.

2. **x-* reserved collision.** F3.1 reserves the ``x-`` prefix for importer/plugin
   metadata (f5-03 §2.7). A legacy key that ALREADY uses an ``x-`` name reserved
   for a core F3.1 field (e.g. ``x-valid-at``) is a case-D incompatibility: the
   validator would reject the merged frontmatter, so the migrator refuses to
   migrate and logs to ``migration_errors.log``.

Owned by #3, stdlib-only (no ruamel, no pydantic).
"""

from __future__ import annotations

# Legacy Obsidian key -> the F3.1 key it semantically collides with. The legacy
# key is PRESERVED (case B); the F3.1 key is ADDED; the pair is logged.
COLLISION_MAP: dict[str, str] = {
    "created": "created_at",
    "modified": "modified_at",  # not an F3.1 core field, but reserved name space
    "publish": "publish_state",  # informational; recorded for human awareness
}

# x-* names reserved for F3.1 core fields. A legacy key using one of these is a
# case-D incompatibility (validator would reject the merged frontmatter).
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

    Only keys that are actually present AND have an F3.1 counterpart in
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