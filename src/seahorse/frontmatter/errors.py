"""Errors for the frontmatter adapter/migrator (#3, F3.3).

These cross the ``frontmatter`` package boundary upward (to the engine write
path, the migrator, and the CLI). They are the loud-rejection surface mandated
by ADR-10: a malformed or MVP-0-incompatible note is never silently accepted
or "auto-fixed" — it raises ``FrontmatterInvalid`` on the read path and a
``MigrationError`` subclass on the migrator path, each carrying the originating
file path so the caller can log it (``migration_errors.log``) or surface it.

Owned by #3. ``E_*`` code constants mirror the CLI exit codes
(``cli/exit_codes.py``, commit 5) and the MCP error codes (``mcp/errors.py``,
commit 5) — parity is enforced there, not here.
"""

from __future__ import annotations

from pathlib import Path


class FrontmatterInvalid(Exception):
    """A note's frontmatter failed F3.1 validation (read path, f5-03 §4.1/§7.2).

    Raised by ``parse_file`` / ``hydrate`` when ``Episode.model_validate`` rejects
    the parsed frontmatter (naive datetime, non-null ``expired_at`` in MVP-0, …).
    The note becomes ilegible to the Engine until a human corrects the field
    (F3.3 does not auto-fix). Carries the source path and the underlying
    ``ValidationError`` for diagnostics.
    """

    code = "E_FRONTMATTER_INVALID"

    def __init__(self, path: Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        # Actionable hint (matrix finding, vault_legacy combo): a legacy Obsidian
        # note (tags/created) or a malformed F3.1 note surfaces only the raw
        # pydantic errors otherwise — no hint for a user who does not know the
        # F3.1 shape. Name the required fields so the fix is discoverable.
        super().__init__(
            f"{self.code}: {path}: {cause} — note is not valid F3.1 frontmatter; "
            "correct it (id, created_at, schema_version, provenance) or remove the note"
        )


class MigrationError(Exception):
    """Base for migrator failures (f5-03 §5.3, bulk-import path)."""

    code = "E_MIGRATION_ABORTED"


class XReservedCollision(MigrationError):
    """A frontmatter ``x-*`` key collides with a core field (e.g. ``x-valid-at``).

    f5-03 §2.7: keys matching ``^x-[a-z0-9-]+$`` are preserved verbatim, EXCEPT
    the reserved collisions (``x-valid-at`` shadows ``valid_at``). The migrator
    refuses these rather than silently dropping or renaming — case D.
    """

    code = "E_X_RESERVED_COLLISION"

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self.key = key
        super().__init__(f"{self.code}: {path}: x-* key '{key}' collides with a core field")


class SubjectEmpty(MigrationError):
    """No subject could be derived (no title, no H1, degenerate filename stem).

    f5-03 §5.6: an empty subject would hash every such note to the same constant
    ``fact_id`` (``sha256("")[:32]``), false-positiving ``query_vigent``. The
    migrator assigns ``title = filename stem`` first; if that is still empty the
    note is case D and is not migrated.
    """

    code = "E_SUBJECT_EMPTY"

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"{self.code}: {path}: no derivable subject (no title/H1/stem)")