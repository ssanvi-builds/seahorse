"""Vault → sidecar rebuild orchestrator.

The ruamel-touching half of the ``.md`` → SQLite index bridge. Scans the vault,
parses each ``.md`` via ``adapter.parse_file`` (ruamel), and builds ruamel-free
``ParsedNote`` payloads for ``SidecarIndexRepository.rebuild_all``. The sidecar
(core) stays ruamel-free; this dependency injection preserves the
ruamel-confinement invariant — the codec is confined to the ``frontmatter``
package, and the only core surface it touches is the ``ParsedNote`` dataclass
(a contract, ruamel-free).

This module is part of the frontmatter adapter and transitively imports ruamel
via ``adapter``; it is NOT imported by ``frontmatter.__init__`` (which stays
ruamel-free for the core's stdlib-only subimports). The CLI imports it
directly::

    from seahorse.frontmatter.rebuild import rebuild_from_vault

Parse failures raise ``FrontmatterInvalid`` (fail-loud honesty: a note that does
not parse is a real error — run ``seahorse frontmatter migrate`` first, then
rebuild). The orchestrator does NOT silently skip unparseable notes; it
surfaces the first failure so the operator fixes the vault rather than shipping
a partial index.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from seahorse.contracts.persistence import ParsedNote, RebuildReport, SidecarIndexRepository
from seahorse.frontmatter.adapter import parse_file
from seahorse.frontmatter.discovery import discover_notes
from seahorse.frontmatter.subject import fact_id_of, normalize_subject, raw_subject


def _parsed_note(vault_root: Path, path: Path) -> ParsedNote:
    # parse_file returns (commented_map, body, episode). The body is byte-a-byte
    # from the file and is NOT attached to the episode (hydrate does that).
    _cm, body, ep = parse_file(path)
    # `subject` and `fact_id` are both Field(exclude=True) — they are NOT
    # serialized to frontmatter; both are DERIVED on read. The .md is the source
    # of truth, so we re-derive them here. CRITICAL: we use the ENGINE's
    # derivation contract (title > H1 > None, NO filename-stem fallback) — i.e.
    # the same `raw_subject` + `normalize_subject` primitives the engine
    # composes in `fact_id_for(body, title)` (engine/collision.py) — NOT the
    # migrator's `derive_subject(title, body, path)` which adds a `path.stem`
    # fallback. This keeps the vault-rebuilt `episode_index.fact_id` EQUAL to
    # the engine-remembered `episodes.fact_id` so conflict enforcement is
    # consistent across both surfaces and there are no phantom conflicts (two
    # no-title notes with the same filename in different dirs would otherwise
    # collide under rebuild but never under the engine). A note with neither
    # title nor H1 derives subject=None -> fact_id=None: not indexed by subject,
    # matching the engine's no-subject contract, and NULL fact_ids never collide
    # under the partial unique index.
    if ep.subject is None:
        raw = raw_subject(ep.title, body or "")
        if raw is not None:
            ep = ep.model_copy(update={"subject": normalize_subject(raw)})
    if ep.fact_id is None and ep.subject:
        ep = ep.model_copy(update={"fact_id": fact_id_of(ep.subject)})
    stat = path.stat()
    return ParsedNote(
        episode=ep,
        file_path=path.relative_to(vault_root).as_posix(),
        mtime_ms=stat.st_mtime_ns // 1_000_000,
        size=stat.st_size,
    )


def iter_parsed_notes(vault_root: Path) -> Iterable[ParsedNote]:
    """Yield a ``ParsedNote`` for every ``.md`` under ``vault_root`` (ruamel-touching).

    Streaming (``discover_notes`` is an ``os.scandir`` iterator, ``parse_file``
    runs lazily as the caller advances) so a large vault does not load every
    note into memory at once. Raises ``FrontmatterInvalid`` on the first note
    that fails on-disk validation (run ``seahorse migrate`` first).
    """
    for path in discover_notes(vault_root):
        yield _parsed_note(vault_root, path)


def rebuild_from_vault(
    vault_root: Path,
    sidecar: SidecarIndexRepository,
    *,
    secondary_index_wipes: Sequence[Callable[[sqlite3.Connection], None]] = (),
) -> RebuildReport:
    """Rebuild the sidecar index from the vault's ``.md`` files.

    Parses each discovered note and delegates the clear-then-rebuild to
    ``sidecar.rebuild_all``. Returns the ``RebuildReport`` (indexed count +
    skipped conflicts). Raises ``FrontmatterInvalid`` on the first unparseable
    note — the vault must be migrated to the on-disk format before rebuild.

    ``secondary_index_wipes`` is forwarded verbatim so the CLI can clear vec0/FTS
    in the same atomic as the episode_index clear — the orchestrator stays a thin
    passthrough.
    """
    return sidecar.rebuild_all(
        iter_parsed_notes(vault_root), secondary_index_wipes=secondary_index_wipes
    )


__all__ = ["iter_parsed_notes", "rebuild_from_vault"]