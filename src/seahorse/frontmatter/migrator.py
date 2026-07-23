"""Vault migrator — legacy notes -> F3.1 frontmatter (f5-03 §3).

Owned by #3. Operacionaliza F3.1 sec 5.3 (autoridad) sobre un vault Obsidian:
clasifica cada ``.md`` (caso A/B/C/D), añade frontmatter bi-temporal aditivo a
las notas legacy (A/B) preservando campos Obsidian, deja intactas las notas
F3.1 (C, idempotente), y rechaza las incompatibles (D, log sin sobreescribir).

**Delegation (plan 5.1=(a)):** el migrador NO llama ``engine.remember``. Escribe
el ``.md`` directamente via ``write_file`` (``.md`` = source of truth; ``index
rebuild`` es el puente a SQLite en commit 4). Es bulk-import aditivo, no una
operación bi-temporal del Engine — ``engine.remember`` generaría un UUIDv7 nuevo
que sobreescribiría el id del migrador.

**Co-escritor humano (§3.7):** MVP-0 corre con ``workers=1`` (escritura
serializada); el paralelizado es MVP-1. El migrador asume Obsidian pausado
(documentado en CLI help, commit 5).

Confined to ``seahorse/frontmatter/``: importa ``adapter``/``handler`` (ruamel).
El migrator NO es re-exportado por ``frontmatter/__init__`` (cargaría ruamel); the
CLI imports it directly (commit 5).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from seahorse.frontmatter.adapter import parse_file, write_file
from seahorse.frontmatter.collisions import (
    detect_legacy_collisions,
    detect_x_reserved_collision,
)
from seahorse.frontmatter.defaults import migration_defaults
from seahorse.frontmatter.discovery import discover_notes
from seahorse.frontmatter.handler import RuamelRTHandler, _make_yaml
from seahorse.frontmatter.manifest import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    ManifestEntry,
    MigrationManifest,
    sha256_of,
    should_skip,
)
from seahorse.frontmatter.subject import derive_subject

# Batch size default: checkpoint the manifest every N notes (resumability,
# f5-03 §6.3). Tuned for a smooth crash-recovery trade-off on a 10k vault.
DEFAULT_BATCH_SIZE = 500


def _iso_z(dt: datetime) -> str:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class VaultMigrator:
    """Migrate a vault's ``.md`` notes to F3.1 frontmatter (f5-03 §3).

    Stateless across notes (the manifest carries per-note state). Construct
    with the vault root and a run ``session_id`` (UUIDv7 per run, f5-03 §3.2);
    call ``run`` to process.
    """

    def __init__(self, vault_root: Path, session_id: str, *, now: datetime | None = None) -> None:
        self.vault_root = vault_root
        self.session_id = session_id
        self._now = now  # injectable for deterministic tests; None -> datetime.now(UTC)
        self._yaml: YAML = _make_yaml()

    # ------------------------------------------------------------------ classify

    def classify(self, path: Path) -> tuple[str, CommentedMap, str]:
        """Return ``(case, baseline_cm, body)`` for a note (f5-03 §3.1).

        - ``case``: one of A/B/C/D.
        - ``baseline_cm``: the parsed legacy frontmatter (empty for A; the
          legacy CommentedMap for B; the F3.1 CommentedMap for C/D).
        - ``body``: the markdown body (byte-a-byte from the file).

        Classify maps syntax/invalid frontmatter into case D (returns, never
        raises) so ``run`` can record the entry and keep going.
        """
        text = path.read_text(encoding="utf-8")
        handler = RuamelRTHandler(self._yaml)
        if not handler.detect(text):
            # Case A: no frontmatter at all.
            return CASE_A, CommentedMap(), text
        try:
            fm, body = handler.split(text)
            cm = handler.load(fm)
        except Exception:
            # ruamel YAMLError -> case D (syntax). Body is unreachable here; the
            # entry records the error and the file is left untouched.
            return CASE_D, CommentedMap(), ""
        keys = set(cm.keys())
        if detect_x_reserved_collision(keys) is not None:
            # x-* reserved collision: case D (validator would reject the merge).
            return CASE_D, cm, body
        if "schema_version" in keys:
            # Has the F3.1 marker. Validate as F3.1: OK+valid_at -> C; otherwise D.
            return self._validate_or_d(path), cm, body
        # No schema_version marker -> legacy frontmatter (case B).
        return CASE_B, cm, body

    def _validate_or_d(self, path: Path) -> str:
        """``schema_version`` is present: C if it validates as F3.1 with valid_at, else D."""
        try:
            _cm, _body, ep = parse_file(path)
        except Exception:
            return CASE_D  # F3.1 marker present but invalid -> incompatible
        if ep.valid_at is not None and ep.schema_version:
            return CASE_C
        return CASE_D  # marker present but incomplete after validation

    # -------------------------------------------------------------- migrate_note

    def migrate_note(self, path: Path, *, dry_run: bool = False) -> ManifestEntry:
        """Process one note; return its manifest entry (f5-03 §3.1/§3.4).

        ``dry_run`` classifies and builds the episode but does NOT write — the
        entry's ``post_hash`` equals ``pre_hash`` and ``migrated_at`` is None.
        """
        pre_hash = sha256_of(path)
        mtime_pre = path.stat().st_mtime
        case, baseline_cm, body = self.classify(path)

        if case == CASE_C:
            # Idempotent: untouched, pre_hash == post_hash, no migrated_at.
            return ManifestEntry(
                path=str(path),
                case=CASE_C,
                pre_hash=pre_hash,
                post_hash=pre_hash,
                mtime_pre=mtime_pre,
                mtime_post=-1,
                migrated_at=None,
            )

        if case == CASE_D:
            # Refused: do NOT overwrite. Log the reason; pre_hash == post_hash.
            return ManifestEntry(
                path=str(path),
                case=CASE_D,
                pre_hash=pre_hash,
                post_hash=pre_hash,
                mtime_pre=mtime_pre,
                mtime_post=-1,
                migrated_at=None,
                error=self._case_d_reason(path, baseline_cm),
            )

        # Case A or B: build the safe MVP-0 episode from defaults.
        file_mtime = datetime.fromtimestamp(mtime_pre, tz=UTC)
        reference_now = self._now if self._now is not None else datetime.now(UTC)
        ep, ts_collisions = migration_defaults(file_mtime, self.session_id, now=reference_now)

        # Subject guard (f5-03 §5.6): refuse to migrate a note whose derived
        # subject is empty (degenerate filename + no title + no H1) — it would
        # collapse to the constant sha256("") fact_id and false query_vigent.
        subject = derive_subject(ep.title, body, path)
        if subject == "":
            return ManifestEntry(
                path=str(path),
                case=CASE_D,
                pre_hash=pre_hash,
                post_hash=pre_hash,
                mtime_pre=mtime_pre,
                mtime_post=-1,
                migrated_at=None,
                error="E_SUBJECT_EMPTY: no title/H1/stem to derive a subject",
            )

        # Legacy collisions (case B only): preserve + add, report (never resolve).
        collisions: list[str] = list(ts_collisions)
        if case == CASE_B:
            collisions.extend(detect_legacy_collisions(set(baseline_cm.keys())))

        if dry_run:
            return ManifestEntry(
                path=str(path),
                case=case,
                pre_hash=pre_hash,
                post_hash=pre_hash,
                mtime_pre=mtime_pre,
                mtime_post=-1,
                migrated_at=None,
                collisions=collisions,
            )

        # Write additively: write_file merges the F3.1 dump onto the legacy
        # baseline_cm (preserving x-*/comments/quote-style for case B, empty for
        # case A), then atomic-writes. mvp="0" -> MVP-0 read path (no expired_at).
        write_file(
            path,
            ep,
            body,
            exclude_none=True,
            baseline_cm=baseline_cm,
            mvp="0",
        )

        post_hash = sha256_of(path)
        mtime_post = path.stat().st_mtime
        return ManifestEntry(
            path=str(path),
            case=case,
            pre_hash=pre_hash,
            post_hash=post_hash,
            mtime_pre=mtime_pre,
            mtime_post=mtime_post,
            migrated_at=_iso_z(reference_now),
            collisions=collisions,
        )

    def _case_d_reason(self, path: Path, cm: CommentedMap) -> str:
        """Best-effort human-readable reason for a case-D classification."""
        x_offender = detect_x_reserved_collision(set(cm.keys()))
        if x_offender is not None:
            return (
                f"E_X_RESERVED_COLLISION: legacy key '{x_offender}' "
                "claims a reserved F3.1 x-* name"
            )
        try:
            parse_file(path)
        except Exception as e:
            return f"E_FRONTMATTER_INVALID: {e}"
        return "E_FRONTMATTER_INVALID: schema_version present but F3.1 validation incomplete"

    # --------------------------------------------------------------------- run

    def run(
        self,
        *,
        dry_run: bool = False,
        resume: bool = False,
        batch_size: int = DEFAULT_BATCH_SIZE,
        manifest_path: Path | None = None,
    ) -> MigrationManifest:
        """Migrate the vault (f5-03 §3). Returns the manifest.

        - ``dry_run``: classify + build, no writes (manifest still produced).
        - ``resume``: load the existing manifest at ``manifest_path`` and skip
          notes whose content is unchanged. The mtime is a cheap pre-filter
          (unchanged mtime -> unchanged content -> skip WITHOUT hashing); the
          hash is the truth, applied only when the mtime changed (f5-03 §3.5).
        - ``batch_size``: checkpoint the manifest every N processed notes.
        - ``manifest_path``: where to read/write the manifest. Defaults to
          ``<vault_root>/.seahorse/migration_manifest.json`` (created on demand).
        """
        if manifest_path is None:
            manifest_path = self.vault_root / ".seahorse" / "migration_manifest.json"

        manifest = MigrationManifest(
            vault_path=str(self.vault_root),
            session_id=self.session_id,
        )
        if resume and manifest_path.exists():
            manifest = MigrationManifest.load(manifest_path)
            manifest.session_id = self.session_id  # this run's id

        notes_iter: Iterator[Path] = discover_notes(self.vault_root)
        notes_list = list(notes_iter)
        manifest.stats.total_notes = len(notes_list)

        processed = 0
        for path in notes_list:
            try:
                current_mtime = path.stat().st_mtime
            except OSError:
                continue  # unreadable/vanished note -> best-effort skip
            if resume:
                prior = manifest.notes.get(str(path))
                if prior is not None:
                    # mtime hint: unchanged mtime -> unchanged content -> skip
                    # without hashing (cheap). mtime_post==-1 for case C/D means
                    # the note was never written, so the hint is unavailable.
                    if prior.mtime_post != -1 and current_mtime == prior.mtime_post:
                        continue
                    # mtime changed (or hint unavailable): hash to be sure.
                    try:
                        pre_hash = sha256_of(path)
                    except OSError:
                        continue
                    if should_skip(prior, pre_hash, current_mtime):
                        continue
            try:
                entry = self.migrate_note(path, dry_run=dry_run)
            except OSError as e:
                # I/O failure mid-read/write: record as case D and keep going —
                # never abort the whole vault run on one note (f5-03 §3.4).
                entry = ManifestEntry(
                    path=str(path),
                    case=CASE_D,
                    pre_hash="sha256:unknown",
                    post_hash="sha256:unknown",
                    mtime_pre=current_mtime,
                    mtime_post=-1,
                    migrated_at=None,
                    error=f"E_IO_FAILED: {e}",
                )
            manifest.add(entry)
            processed += 1
            if batch_size > 0 and processed % batch_size == 0 and not dry_run:
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest.save(manifest_path)

        if not dry_run:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest.save(manifest_path)
        return manifest