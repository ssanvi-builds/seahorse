"""Materializer — episodes → F3.1 ``.md`` notes in the vault.

The materializer is the write side of the vault contract: it turns engine
episodes into visible, editable Obsidian notes (``{dir}/{subject-slug}.md``)
and keeps them honest across the episode lifecycle. It reuses the frontmatter
adapter's ``serialize`` / ``write_file`` (round-trip ruamel, atomic tmp +
``os.replace``) — the migrator is the only other caller of ``write_file``.

What the materializer owns:
- **Write on ACTIVE**: ``materialize(ep)`` writes the note for an episode the
  write path / distill / improve produced. The mode filter decides which
  episodes become notes (``consolidated``: distilled knowledge + project notes;
  ``all``: every ACTIVE episode; ``off``: none).
- **Invalidate on forget/improve**: ``invalidate(ep)`` re-reads the materialized
  note and merges the episode's updated frontmatter (``invalid_at`` set) onto
  the baseline, preserving the current body — a human body edit survives. This
  is the C1 fix: without it, a later ``index rebuild`` would re-parse the stale
  note (``invalid_at=None``) and resurrect the invalidated episode.
- **Idempotency + collision handling**: the human-edit guard compares the
  existing note's **frontmatter id** with the episode id (NOT mtimes — a
  seahorse-written note always has ``mtime > created_at``). Same id → already
  materialized (skip); different id → a foreign note or another episode → never
  overwrite, write to ``{slug}-{id8}.md``; that also taken → report the
  collision (the operator resolves, the rebuild reports conflicts).
- **Stable title for consolidated notes**: a consolidated episode's engine title
  carries the ``[session_tag:n]`` suffix while its subject is the stable cluster
  key (distill.py). The materializer serializes an *effective episode* whose
  title is the subject for ``extraction_mode=consolidated``, so the rebuild
  derives the SAME ``fact_id`` as the engine (C2). The same effective episode is
  used by ``invalidate`` so an invalidation merge never clobbers the stable
  title back to the suffixed one.
- **Path registration**: ``sidecar.put_path`` records the note's path + mtime so
  ``invalidate`` can find it and the rebuild can reconcile.

Best-effort contract (M9): the materializer NEVER fails the write path — an
``OSError`` is logged and reported in the ``MaterializeResult``; the episode
lives in SQLite regardless. ``seahorse materialize`` is the backfill.

Ruamel-confinement: this module is part of the frontmatter package (it imports
``adapter``), so it may touch ruamel. The sidecar it talks to is a core
contract (ruamel-free) — the materializer imports it via a structural Protocol.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import parse_file, serialize, write_file

_logger = logging.getLogger("seahorse.frontmatter.materialize")

# Slug: lowercase, keep ``[a-z0-9]``, collapse every other run to a single
# dash, strip leading/trailing dashes. The slug is a FILENAME, not a display
# name — the frontmatter ``title``/``subject`` carry the human-readable form.
_SLUG_KEEP = re.compile(r"[^a-z0-9]+")
_SLUG_STRIP = re.compile(r"^-+|-+$")

# The collision-suffix length (``{slug}-{id8}.md``). Birthday bound ~2^16 —
# a collision on the suffixed name is reported, never silently resolved.
_ID8 = 8

# Materialization modes (mirror the config Literal).
MODE_CONSOLIDATED = "consolidated"
MODE_ALL = "all"
MODE_OFF = "off"


@runtime_checkable
class _SidecarLike(Protocol):
    """The ``episode_paths`` surface the materializer needs (structural)."""

    def put_path(self, ep_id: str, file_path: str, mtime_ms: int, size: int) -> None: ...

    def get_path(self, ep_id: str) -> tuple[str, int, int] | None: ...


def slugify(subject: str) -> str:
    """A filesystem-safe slug for a subject (empty when nothing survives)."""
    slug = _SLUG_KEEP.sub("-", subject.lower())
    return _SLUG_STRIP.sub("", slug)


def _effective_episode(ep: Episode) -> Episode:
    """The serialized form of ``ep``: stable title for consolidated notes (C2).

    A consolidated episode's engine title carries the ``[session_tag:n]``
    suffix while its subject is the stable cluster key. Writing the note with
    ``title=subject`` makes the rebuild derive the SAME ``fact_id`` as the
    engine (the rebuild derives subject from title first). All other episodes
    keep their engine title.
    """
    if ep.provenance.get("extraction_mode") == "consolidated" and ep.subject:
        return ep.model_copy(update={"title": ep.subject})
    return ep


@dataclass(frozen=True)
class MaterializeResult:
    """Outcome of materializing / invalidating one episode.

    ``status``: ``written`` | ``invalidated`` | ``skipped`` | ``collision`` |
    ``error``. ``path`` is the vault-relative note path (when one exists);
    ``reason`` explains a skip / collision / error.
    """

    ep_id: str
    status: str
    path: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MaterializeReport:
    """Batch outcome (``seahorse materialize`` backfill)."""

    written: int = 0
    items: list[MaterializeResult] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return len(self.items) - self.written


class Materializer:
    """Writes episodes as F3.1 notes and keeps them honest across the lifecycle.

    Construct with the vault root, the vault-relative target ``dir``, the
    ``episode_paths`` sidecar, and the materialization ``mode``. ``materialize``
    is the ACTIVE-write hook; ``invalidate`` is the forget/improve hook.
    """

    def __init__(
        self,
        vault_root: Path,
        *,
        dir: str,
        sidecar: _SidecarLike,
        mode: str = MODE_CONSOLIDATED,
    ) -> None:
        self._vault_root = vault_root
        self._dir = dir
        self._sidecar = sidecar
        self._mode = mode

    # ------------------------------------------------------------------ write

    def materialize(self, ep: Episode) -> MaterializeResult:
        """Write the F3.1 note for ``ep`` (idempotent, best-effort).

        Mode filter first (``off`` / non-matching episodes skip), then the
        subject guard (``subject=None`` episodes are degenerate for naming),
        then the id-based collision guard (C3). Never raises on I/O — an
        ``OSError`` is logged and reported.
        """
        if self._mode == MODE_OFF:
            return MaterializeResult(ep.id, "skipped", reason="mode_off")
        if not self._should_materialize(ep):
            return MaterializeResult(ep.id, "skipped", reason="mode_filter")
        if not ep.subject:
            return MaterializeResult(ep.id, "skipped", reason="no_subject")

        slug = slugify(ep.subject) or ep.id[:_ID8]
        target = self._vault_root / self._dir / f"{slug}.md"
        existing_id = self._existing_id(target)
        if existing_id == ep.id:
            # Our own materialization — the note is current (episodes are
            # append-only), so backfill is idempotent.
            return MaterializeResult(ep.id, "skipped", reason="already_materialized")
        if existing_id is not None:
            # A foreign note (or another episode) owns the slug — never
            # overwrite; fall back to the id8 suffix.
            target = self._vault_root / self._dir / f"{slug}-{ep.id[:_ID8]}.md"
            if self._existing_id(target) is not None:
                return MaterializeResult(
                    ep.id, "collision", reason="slug_and_id8_taken"
                )
        try:
            self._write(target, ep)
        except OSError as exc:
            _logger.warning(
                "materialize.failed ep_id=%s path=%s error=%s", ep.id, target, exc
            )
            return MaterializeResult(ep.id, "error", reason=str(exc))
        self._register(target, ep.id)
        return MaterializeResult(
            ep.id, "written", path=self._rel(target)
        )

    def materialize_episodes(self, eps: Iterable[Episode]) -> MaterializeReport:
        """Batch materialization (backfill). Deterministic order."""
        items = [self.materialize(ep) for ep in eps]
        return MaterializeReport(
            written=sum(1 for r in items if r.status == "written"), items=items
        )

    # -------------------------------------------------------------- invalidate

    def invalidate(self, ep: Episode) -> MaterializeResult | None:
        """Merge the episode's ``invalid_at`` into its materialized note (C1).

        A merge, not an overwrite: the current body (including a human edit) is
        preserved; only changed schema fields (``invalid_at``) are updated. The
        rebuild then reads ``invalid_at`` from the note and indexes the episode
        as invalid — no resurrection. No-op when the episode was never
        materialized (no registered path) or the note has vanished.
        """
        path = self._path_for(ep.id)
        if path is None or not path.exists():
            return None
        try:
            baseline_cm, body, _ = parse_file(path)
        except Exception as exc:  # noqa: BLE001 — best-effort, never fail the write
            _logger.warning(
                "materialize.invalidate_parse_failed ep_id=%s path=%s error=%s",
                ep.id,
                path,
                exc,
            )
            return MaterializeResult(ep.id, "error", reason=f"invalidate_parse: {exc}")
        try:
            write_file(
                path,
                _effective_episode(ep),
                body,
                exclude_none=True,
                baseline_cm=baseline_cm,
            )
        except OSError as exc:
            _logger.warning(
                "materialize.invalidate_failed ep_id=%s path=%s error=%s",
                ep.id,
                path,
                exc,
            )
            return MaterializeResult(ep.id, "error", reason=str(exc))
        self._register(path, ep.id)
        return MaterializeResult(ep.id, "invalidated", path=self._rel(path))

    # ------------------------------------------------------------------ misc

    def _should_materialize(self, ep: Episode) -> bool:
        if self._mode == MODE_ALL:
            return True
        # consolidated (default): distilled knowledge + project notes — the
        # knowledge the agent works with, not the session noise.
        if ep.provenance.get("extraction_mode") == "consolidated":
            return True
        return ep.cognitive_type == "project_doc"

    def _existing_id(self, path: Path) -> str | None:
        """The frontmatter id of an existing note, or ``None`` if absent.

        An unparseable file returns ``""`` — treated as foreign (never
        overwritten; the collision suffix path handles it).
        """
        if not path.exists():
            return None
        try:
            _cm, _body, ep = parse_file(path)
        except Exception:  # noqa: BLE001 — a broken note is foreign, not ours
            return ""
        return ep.id

    def _path_for(self, ep_id: str) -> Path | None:
        row = self._sidecar.get_path(ep_id)
        if row is None:
            return None
        return self._vault_root / row[0]

    def _write(self, target: Path, ep: Episode) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        serialize(_effective_episode(ep), target, exclude_none=True)

    def _register(self, target: Path, ep_id: str) -> None:
        stat = target.stat()
        self._sidecar.put_path(
            ep_id, self._rel(target), stat.st_mtime_ns // 1_000_000, stat.st_size
        )

    def _rel(self, target: Path) -> str:
        return target.relative_to(self._vault_root).as_posix()


__all__ = [
    "Materializer",
    "MaterializeResult",
    "MaterializeReport",
    "slugify",
    "MODE_CONSOLIDATED",
    "MODE_ALL",
    "MODE_OFF",
]
