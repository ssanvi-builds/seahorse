"""Errors for the frontmatter adapter/migrator.

These cross the ``frontmatter`` package boundary upward (to the engine write
path, the migrator, and the CLI). They are the loud-rejection surface mandated
by the fail-loud principle: a malformed or first-release-incompatible note is
never silently accepted or "auto-fixed" — it raises ``FrontmatterInvalid`` on
the read path and a ``MigrationError`` subclass on the migrator path, each
carrying the originating file path so the caller can log it
(``migration_errors.log``) or surface it.

``E_*`` code constants mirror the CLI exit codes (``cli/exit_codes.py``) and the
MCP error codes (``mcp/errors.py``) — parity is enforced there, not here.
"""

from __future__ import annotations

from pathlib import Path


class FrontmatterInvalid(Exception):
    """A note's frontmatter failed on-disk format validation (read path).

    Raised by ``parse_file`` / ``hydrate`` when ``Episode.model_validate`` rejects
    the parsed frontmatter (naive datetime, non-null ``expired_at`` in the first
    release, …). The note becomes unreadable to the engine until a human
    corrects the field (the frontmatter layer does not auto-fix). Carries the
    source path and the underlying ``ValidationError`` for diagnostics.
    """

    code = "E_FRONTMATTER_INVALID"

    def __init__(self, path: Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        # Actionable hint: a legacy Obsidian note (tags/created) or a malformed
        # on-disk note surfaces only the raw pydantic errors otherwise — no hint
        # for a user who does not know the expected frontmatter shape. Name the
        # required fields so the fix is discoverable.
        super().__init__(
            f"{self.code}: {path}: {cause} — note is not valid frontmatter; "
            "correct it (id, created_at, schema_version, provenance) or remove the "
            "note; for legacy Obsidian notes without canonical frontmatter, run "
            "`seahorse frontmatter migrate`"
        )


class MigrationError(Exception):
    """Base for migrator failures (bulk-import path)."""

    code = "E_MIGRATION_ABORTED"


class XReservedCollision(MigrationError):
    """A frontmatter ``x-*`` key collides with a core field (e.g. ``x-valid-at``).

    Keys matching ``^x-[a-z0-9-]+$`` are preserved verbatim, EXCEPT the reserved
    collisions (``x-valid-at`` shadows ``valid_at``). The migrator refuses these
    rather than silently dropping or renaming.
    """

    code = "E_X_RESERVED_COLLISION"

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self.key = key
        super().__init__(f"{self.code}: {path}: x-* key '{key}' collides with a core field")


class SubjectEmpty(MigrationError):
    """No subject could be derived (no title, no H1, degenerate filename stem).

    An empty subject would hash every such note to the same constant ``fact_id``
    (``sha256("")[:32]``), creating false positives in the current-state query
    (``query_vigent``). The migrator assigns ``title = filename stem`` first; if
    that is still empty the note is case D and is not migrated.
    """

    code = "E_SUBJECT_EMPTY"

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"{self.code}: {path}: no derivable subject (no title/H1/stem)")