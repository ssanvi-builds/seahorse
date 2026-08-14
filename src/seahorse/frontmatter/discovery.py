"""Vault note discovery.

Part of the frontmatter migrator, stdlib-only. Streams ``.md`` paths under a
vault root via ``os.scandir`` (not ``glob``/``walk`` loading everything into
memory — streaming matters for large vaults). Excludes Obsidian/plugin
directories that are not user notes:

- ``.obsidian`` — Obsidian config/plugins/workspace.
- ``.trash`` — Obsidian's soft-delete bin.
- ``.git`` and other VCS metadata.
- ``.seahorse`` — the sidecar SQLite/index directory (engine-owned, not a note).

Yields paths in sorted order within each directory for deterministic migration
runs (the manifest's pre/post hashes must be reproducible).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

# Directory basenames that are never note sources. Compared case-insensitively
# because macOS HFS+/APFS is case-insensitive by default.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {".obsidian", ".trash", ".git", ".seahorse", ".svn", ".hg", "_darcs"}
)


def discover_notes(vault_root: Path) -> Iterator[Path]:
    """Yield every ``.md`` file under ``vault_root``, excluding plugin dirs.

    Streaming (``os.scandir`` recursion, not ``glob('*')``), sorted per
    directory for determinism. ``vault_root`` itself is never excluded (the
    caller chooses the root; its own basename is irrelevant — only descendants
    are matched against ``_EXCLUDED_DIRS``).
    """
    if not vault_root.exists():
        return
    yield from _scan_dir(vault_root)


def _scan_dir(directory: Path) -> Iterator[Path]:
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        # Unreadable directory (permission, vanished): skip silently rather than
        # aborting the whole vault scan. The manifest's error count captures
        # per-note failures elsewhere; an unreadable dir is a best-effort skip.
        return
    for entry in entries:
        name = entry.name
        if entry.is_dir(follow_symlinks=False):
            if name.lower() in _EXCLUDED_DIRS:
                continue
            # Don't follow symlinked directories (avoid cycles on messy vaults).
            if entry.is_symlink():
                continue
            yield from _scan_dir(Path(entry.path))
        elif entry.is_file(follow_symlinks=False) and name.endswith(".md"):
            yield Path(entry.path)