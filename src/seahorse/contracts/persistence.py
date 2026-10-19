"""Persistence contracts — the repository seams that earned their keep.

Inventory verdict (C3 deepening, 2026-10-19 — "two adapters means a real
seam; one means a hypothetical one"):

- ``EpisodeIndexRepository`` / ``VectorIndexRepository`` /
  ``FullTextIndexRepository``: Protocol. Each has a SQLite implementation
  AND in-memory test doubles across the disclosure/retrieval suites (the
  interface IS their test surface). Signatures are signed contracts.
- Collapsed to the concrete SQLite types (single adapter, no test doubles —
  the Protocol was a mirror of the implementation): ``AuditEventRepository``
  (engine consumer → ``SqliteAuditEventRepository``),
  ``SidecarIndexRepository`` (frontmatter rebuild consumer →
  ``SqliteSidecarIndexRepository``), ``EmbeddingsCacheRepository``
  (embeddings cache consumer → ``SqliteEmbeddingsCacheRepository``).
- Deleted: ``ReindexJobRepository`` — a Protocol typed by NO consumer (the
  storage accessor was already concrete). ``ReindexJob`` stays: it is the
  payload the concrete repo returns and the e2e smoke exercises.

The frozen dataclasses at the top are payloads contracts (returned by / handed
to the persistence layer), not seams.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

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
# (no own connection). SQLite impl: ``persistence/vector_index.py``
# (sqlite-vec); a later embedder stack swaps in over the same Protocol.
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
# SQLite impl: ``persistence/fts_index.py`` (FTS5 + BM25); a later engine
# stack swaps in over the same Protocol.
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
# AuditEventRepository / SidecarIndexRepository / EmbeddingsCacheRepository /
# ReindexJobRepository — collapsed or deleted (see module docstring).
# ``ReindexJob`` stays as the concrete repo's payload contract.
# ---------------------------------------------------------------------------


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

    ``body_hash`` is the note body's canonical hash (``engine.canonical``,
    body-only) computed by the orchestrator at parse time — the anchor the
    rebuild compares against a live episode's body to detect human edits
    (``RebuildDivergence``). Additive and optional: builders that do not know
    the hash (or callers that never compare) leave ``None``.
    """

    episode: Episode
    file_path: str
    mtime_ms: int
    size: int
    body_hash: str | None = None


@dataclass(frozen=True)
class RebuildDivergence:
    """A materialized note whose canonical body hash differs from the live
    episode's body — a human edit the index cache cannot see.

    Proposal-only: the rebuild REPORTS the suggested ``seahorse improve``
    per note and never applies it. The vault is the source of truth, and a
    human correction enters the knowledge base through the write path (human
    provenance, a new superseding episode) — never an automatic overwrite.
    """

    ep_id: str
    file_path: str


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
    """Result of ``SqliteSidecarIndexRepository.rebuild_all`` (clear-then-rebuild).

    Clear-then-rebuild (not upsert) keeps ``.md`` the source of truth and the
    SQLite index a derived cache: ``episode_index`` + ``episode_paths`` are
    wiped and repopulated from the parsed notes each rebuild, so deletions and
    edits in the vault propagate without a diff. ``rebuild_all`` does NOT touch
    ``episodes`` (``episodes`` is the engine hot-path cache, populated by
    ``remember``; the index is the vault-backed surface).

    ``indexed`` counts the notes that landed in the index; ``skipped`` lists
    the conflict group members left out. A non-empty ``skipped`` is a signal to
    the operator, never a silent no-op.

    ``divergences`` (P1b) lists materialized notes whose canonical body hash
    differs from the live episode's body — human edits awaiting promotion,
    proposed per note, never applied (see ``RebuildDivergence``). Empty unless
    the caller handed the orchestrator a ``live_body`` lookup.
    """

    indexed: int
    skipped: list[RebuildConflict] = field(default_factory=list)
    divergences: list[RebuildDivergence] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Collapsed/deleted (see module docstring): AuditEventRepository → engine
# accepts ``SqliteAuditEventRepository``; SidecarIndexRepository → frontmatter
# rebuild accepts ``SqliteSidecarIndexRepository``; EmbeddingsCacheRepository
# → ``SqliteEmbeddingsCacheRepository``; ReindexJobRepository deleted (no
# typed consumer). ``ReindexJob`` stays as the concrete repo's payload.
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
