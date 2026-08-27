"""The nine repository Protocols owned by the persistence layer.

These are the storage frontier the persistence layer owns and the other
components (the embedder, progressive disclosure, the BFS axis, hybrid
retrieval) consume. Signatures are signed contracts; do NOT deviate.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from seahorse.contracts.engine import AuditEvent
from seahorse.contracts.episode import Episode
from seahorse.contracts.index import IndexRowData, PITKind

# ---------------------------------------------------------------------------
# Typed payloads returned by the vector / FTS repos (frozen).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VectorHit:
    """kNN hit. ``score = 1/(1+distance)`` is computed by the implementation."""

    ep_id: str
    distance: float
    score: float


@dataclass(frozen=True)
class FtsDoc:
    """Insert payload for FullTextIndexRepository.upsert."""

    ep_id: str
    body_md: str
    title: str | None = None
    tags: list[str] = field(default_factory=list)  # serialized as ' '.join(tags)
    summary: str | None = None
    subject: str | None = None


@dataclass(frozen=True)
class FullTextHit:
    """BM25 hit. ``score = exp(-bm25_score)`` (reproducible)."""

    ep_id: str
    bm25_score: float
    score: float


# ---------------------------------------------------------------------------
# EpisodeIndexRepository — 7 accessors + bfs_neighbors_state_at
# Owned by the persistence layer; consumed by progressive disclosure (the BFS
# axis delegates BFS to bfs_neighbors_state_at).
# ---------------------------------------------------------------------------


@runtime_checkable
class EpisodeIndexRepository(Protocol):
    """Typed accessor over ``episode_index`` (no body). Owned by the persistence
    layer; consumed by progressive disclosure."""

    # INDEX level (1st call, within the hybrid retrieval latency budget)
    def get_rows(self, ep_ids: Sequence[str]) -> list[IndexRowData]: ...
    def get_rows_state_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]: ...
    def get_rows_known_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]: ...

    # Session-scoped read (the two-stage session→episode re-rank): all rows of
    # one session (SQL WHERE session_id = ?). Denormalized by migration 012.
    def get_rows_by_session(self, session_id: str) -> list[IndexRowData]: ...

    # TIMELINE level (current-release axes)
    def chain_rows_from(self, ep_id: str) -> list[IndexRowData]: ...
    def find_vigent_row_by_fact_id(
        self, fact_id: str, exclude: str | None = None
    ) -> IndexRowData | None: ...

    # TIMELINE level (later-release axes — revisable until then)
    def range_rows_state_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]: ...
    def range_rows_known_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]: ...

    # Recursive-CTE BFS, owned by the persistence layer; the BFS axis owns the
    # GraphRepository contract and delegates here. hops <= MAX_HOPS_MVP1;
    # include_tags_soft is a medium-term goal (raises NotImplementedError in the
    # current release when True).
    def bfs_neighbors_state_at(
        self,
        ep_id: str,
        pit: datetime,
        *,
        pit_kind: PITKind,
        hops: int,
        include_tags_soft: bool,
    ) -> list[IndexRowData]: ...


# ---------------------------------------------------------------------------
# VectorIndexRepository — fold-into-upsert + distinct_model_identities
# Owned by the persistence layer; operated by the embedder via this Protocol
# (no own connection). Current release: Protocol materialized; SQLite impl
# deferred to a later release (no sqlite-vec dep).
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorIndexRepository(Protocol):
    def upsert(
        self,
        ep_id: str,
        vector: bytes,
        *,
        dim: int,
        model_identity: str,
        content_hash: str,
        embedded_at: str,
    ) -> None: ...
    def distinct_model_identities(self) -> list[str]: ...
    def knn(
        self,
        query: Any,
        k: int,
        *,
        vigent_only: bool = True,
        fact_id_filter: str | None = None,
        cognitive_types: list[str] | None = None,
    ) -> list[VectorHit]: ...
    def knn_state_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]: ...
    def knn_known_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]: ...
    def remove_for_rebuild(self) -> None: ...
    def rebuild(self) -> None: ...
    def count(self) -> int: ...


# ---------------------------------------------------------------------------
# FullTextIndexRepository
# Current release: Protocol materialized; SQLite impl deferred to a later
# release (no FTS5 dep).
# ---------------------------------------------------------------------------


@runtime_checkable
class FullTextIndexRepository(Protocol):
    def upsert(self, doc: FtsDoc) -> None: ...
    def search(
        self, query: str, k: int, *, vigent_only: bool = True, subject_filter: str | None = None
    ) -> list[FullTextHit]: ...
    def search_state_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]: ...
    def search_known_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]: ...
    def remove_for_rebuild(self, ep_id: str) -> None: ...
    def rebuild(self, docs: list[FtsDoc]) -> None: ...
    def count(self) -> int: ...


# ---------------------------------------------------------------------------
# AuditEventRepository. AuditEvent type defined by the engine (the persistence
# layer stores).
# ---------------------------------------------------------------------------


@runtime_checkable
class AuditEventRepository(Protocol):
    def append(self, event: AuditEvent) -> None: ...
    def query(
        self,
        *,
        target_id: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditEvent]: ...


# ---------------------------------------------------------------------------
# SidecarIndexRepository. episode_paths + episode_index maintenance.
# Consumed by the frontmatter adapter; the persistence layer owns and maintains
# it. Typed methods, no raw SQL.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedNote:
    """Ruamel-free payload for ``SidecarIndexRepository.rebuild_all``.

    Built by the frontmatter orchestrator from ``adapter.parse_file`` and handed
    to the sidecar so the persistence layer repopulates ``episode_index`` +
    ``episode_paths`` WITHOUT importing ruamel (ruamel-confinement invariant:
    the codec is confined to ``frontmatter.handler``/``frontmatter.adapter``;
    the sidecar is core and stays ruamel-free). Carries the parsed ``Episode``
    (a core contract, ruamel-free) plus the file metadata the sidecar owns.

    ``skip_extraction`` is derived by the sidecar from
    ``episode.provenance["extraction_mode"] == "skip"``, matching the disclosure
    shaper — so a migrated note (migrator default ``extraction_mode=skip``)
    lands with ``skip_extraction=1`` (excluded from the FTS5 + embedding queue).
    """

    episode: Episode
    file_path: str
    mtime_ms: int
    size: int


@dataclass(frozen=True)
class RebuildConflict:
    """A note skipped during rebuild because it would violate an invariant.

    Rebuild is NOT silent and does NOT auto-resolve. A duplicate currently valid
    ``fact_id`` (the partial unique ``uq_episode_index_active_per_subject``) is
    reported, not picked — the operator decides which note wins. ALL members of a
    conflict group are skipped (no auto-pick of a winner), so the index never
    carries an arbitrary choice the vault did not make.
    """

    ep_id: str
    file_path: str
    fact_id: str
    reason: str


@dataclass(frozen=True)
class RebuildReport:
    """Result of ``SidecarIndexRepository.rebuild_all`` (clear-then-rebuild).

    Clear-then-rebuild (not upsert) keeps ``.md`` the source of truth and the
    SQLite index a derived cache: ``episode_index`` + ``episode_paths`` are
    wiped and repopulated from the parsed notes each rebuild, so deletions and
    edits in the vault propagate without a diff. ``rebuild_all`` does NOT touch
    ``episodes`` (``episodes`` is the engine hot-path cache, populated by
    ``remember``; the index is the vault-backed surface).

    ``indexed`` counts the notes that landed in the index; ``skipped`` lists
    the conflict group members left out. A non-empty ``skipped`` is a signal to
    the operator, never a silent no-op.
    """

    indexed: int
    skipped: list[RebuildConflict] = field(default_factory=list)


@runtime_checkable
class SidecarIndexRepository(Protocol):
    def put_path(self, ep_id: str, file_path: str, mtime_ms: int, size: int) -> None: ...
    def get_path(self, ep_id: str) -> tuple[str, int, int] | None: ...

    @contextmanager
    def reindex(self, ep_id: str, file_path: str, mtime_ms: int, size: int) -> Iterator[None]: ...

    def rebuild_all(
        self,
        notes: Iterable[ParsedNote],
        *,
        secondary_index_wipes: Sequence[Callable[[sqlite3.Connection], None]] = (),
    ) -> RebuildReport: ...
        # Clear episode_index + episode_paths and repopulate from notes (ruamel-free;
        # the caller builds ParsedNote from parsed .md files). See RebuildReport
        # + RebuildConflict for the honesty contract (never silent).
        # secondary_index_wipes runs caller-supplied wipe hooks in the clear phase
        # so vec0/FTS do not leave ghost hits on a vault rebuild.


# ---------------------------------------------------------------------------
# EmbeddingsCacheRepository. Key: (content_hash, model_identity, role).
# Owned by the persistence layer; operated by the embedder via this Protocol
# (no own connection).
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingsCacheRepository(Protocol):
    def batch_lookup(
        self, model_identity: str, role: str, content_hashes: Sequence[str]
    ) -> dict[str, bytes]: ...
    def batch_insert(
        self,
        model_identity: str,
        role: str,
        content_hashes: Sequence[str],
        vectors: Sequence[bytes],
    ) -> None: ...
    def count(self) -> int: ...
    def trim(self, max_rows: int) -> None: ...


# ---------------------------------------------------------------------------
# ReindexJobRepository. reindex_jobs DDL. Owned by the persistence layer,
# operated by the embedder. Methods are setters in the current release (no
# state-transition guards).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReindexJob:
    job_id: int
    model_from: str
    model_to: str
    total: int
    done: int
    status: str  # running | paused | done | failed
    started_at: str
    finished_at: str | None


@runtime_checkable
class ReindexJobRepository(Protocol):
    def create(self, *, model_from: str, model_to: str, total: int) -> int: ...
    def start(self, job_id: int) -> None: ...
    def pause(self, job_id: int) -> None: ...
    def finish(self, job_id: int) -> None: ...
    def fail(self, job_id: int) -> None: ...
    def list(self, *, status: str | None = None) -> list[ReindexJob]: ...
