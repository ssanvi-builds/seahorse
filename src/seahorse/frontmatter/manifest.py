"""Migration manifest + resume (f5-03 §3.5).

Owned by #3, stdlib-only (json + hashlib + dataclasses). The manifest is the
vault-level idempotency record: one entry per processed note with pre/post
SHA-256, mtimes, case, and collisions. ``--resume`` re-runs only notes whose
content changed since the last manifest (hash truth, mtime hint) — f5-03 §3.5.

Why SHA-256 AND mtime: mtime is manipulable and Obsidian rewrites it via the
Property UI; the hash is content truth, the mtime is a cheap hint to avoid
re-hashing every note on resume. ``should_skip`` is the resume predicate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from seahorse.frontmatter.defaults import MANIFEST_VERSION, MIGRATOR_VERSION, SCHEMA_VERSION_MVP0

# Case labels written to the manifest (f5-03 §3.1).
CASE_A = "A"  # no frontmatter -> added
CASE_B = "B"  # non-F3.1 frontmatter -> preserved + added
CASE_C = "C"  # F3.1 already present -> no-op (idempotent)
CASE_D = "D"  # incompatible -> refused, logged


@dataclass(frozen=True)
class ManifestEntry:
    """One note's migration record (f5-03 §3.5)."""

    path: str
    case: str
    pre_hash: str  # "sha256:<hex>" of the file before this run
    post_hash: str  # "sha256:<hex>" after (== pre_hash for case C / D)
    mtime_pre: float  # epoch seconds, or -1 when unknown
    mtime_post: float  # epoch seconds, or -1 when not written (C/D)
    migrated_at: str | None  # ISO-8601 Z, or None for C (no write)
    collisions: list[str] = field(default_factory=list)
    error: str | None = None  # case D reason (None otherwise)


@dataclass
class MigrationStats:
    """Aggregate counts for the manifest ``stats`` block (f5-03 §3.5)."""

    total_notes: int = 0
    migrated: int = 0  # A + B
    already_f31: int = 0  # C
    errors: int = 0  # D
    collisions: int = 0  # notes with >=1 collision


@dataclass
class MigrationManifest:
    """The vault-level manifest (f5-03 §3.5)."""

    manifest_version: str = MANIFEST_VERSION
    vault_path: str = ""
    schema_version: str = SCHEMA_VERSION_MVP0
    migrator_version: str = MIGRATOR_VERSION
    session_id: str = ""
    stats: MigrationStats = field(default_factory=MigrationStats)
    notes: dict[str, ManifestEntry] = field(default_factory=dict)

    def add(self, entry: ManifestEntry) -> None:
        """Record/replace a note entry and update aggregate stats."""
        prev = self.notes.get(entry.path)
        if prev is not None:
            self._undo_stats(prev)
        self.notes[entry.path] = entry
        self._apply_stats(entry)

    def to_json(self) -> str:
        """Serialize as the canonical manifest JSON (sorted, indented)."""
        notes_sorted = sorted(self.notes.values(), key=lambda e: e.path)
        payload: dict[str, Any] = {
            "manifest_version": self.manifest_version,
            "vault_path": self.vault_path,
            "schema_version": self.schema_version,
            "migrator_version": self.migrator_version,
            "session_id": self.session_id,
            "stats": asdict(self.stats),
            "notes": [asdict(e) for e in notes_sorted],
        }
        return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> MigrationManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        stats = MigrationStats(**data.get("stats", {}))
        notes = {}
        for n in data.get("notes", []):
            e = ManifestEntry(
                path=n["path"],
                case=n["case"],
                pre_hash=n["pre_hash"],
                post_hash=n["post_hash"],
                mtime_pre=n.get("mtime_pre", -1),
                mtime_post=n.get("mtime_post", -1),
                migrated_at=n.get("migrated_at"),
                collisions=list(n.get("collisions", [])),
                error=n.get("error"),
            )
            notes[e.path] = e
        return cls(
            manifest_version=data.get("manifest_version", MANIFEST_VERSION),
            vault_path=data.get("vault_path", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION_MVP0),
            migrator_version=data.get("migrator_version", MIGRATOR_VERSION),
            session_id=data.get("session_id", ""),
            stats=stats,
            notes=notes,
        )

    def _apply_stats(self, entry: ManifestEntry) -> None:
        if entry.case in (CASE_A, CASE_B):
            self.stats.migrated += 1
        elif entry.case == CASE_C:
            self.stats.already_f31 += 1
        elif entry.case == CASE_D:
            self.stats.errors += 1
        if entry.collisions:
            self.stats.collisions += 1

    def _undo_stats(self, entry: ManifestEntry) -> None:
        if entry.case in (CASE_A, CASE_B):
            self.stats.migrated -= 1
        elif entry.case == CASE_C:
            self.stats.already_f31 -= 1
        elif entry.case == CASE_D:
            self.stats.errors -= 1
        if entry.collisions:
            self.stats.collisions -= 1


def sha256_of(path: Path) -> str:
    """Return ``"sha256:<hex>"`` of the file's bytes (f5-03 §3.5)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def should_skip(entry: ManifestEntry, current_hash: str, current_mtime: float) -> bool:
    """Resume predicate (f5-03 §3.5).

    Skip when the note's content is unchanged since the manifest was written
    (``current_hash == post_hash``). A changed mtime alone does NOT force a
    re-run — the hash is truth; the mtime is only a cheap pre-filter (a note
    whose mtime is unchanged cannot have changed content, so we skip without
    hashing). When the hash differs the note is re-processed regardless of mtime.
    """
    # Content truth: skip iff unchanged; a differing hash re-processes regardless
    # of mtime (the mtime is only a cheap pre-filter, applied upstream).
    return current_hash == entry.post_hash