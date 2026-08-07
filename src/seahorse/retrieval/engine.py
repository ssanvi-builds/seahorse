"""#11 Hybrid Retrieval — the ``recall`` entrypoint + 2-stage orchestration.

The cima do fosso competitivo MVP-1 (f5-11). ``recall`` takes a ``query`` and
optional ``PITPoint`` / ``cognitive_type`` / ``anchor_ep_id``, invokes the
query-driven sources (kNN via #6 over the query embedding, BM25 via #6),
optionally expands with the anchor-driven stage-2 sources (the ``supersedes``
chain of #2; the BFS of #10 as a mediano extension), and fuses everything with
``rrf_fuse`` producing ``list[FusedCandidate]`` (no body) — the seam #8
``materialize_index`` consumes.

2-STAGE model (f5-11 §4, correction adversaria HIGH 1):

- **Stage 1** (query-driven): kNN + BM25. Both are query-based, so they do NOT
  need an anchor. NOTE: the spec (§9.2/§14.1) shows ``asyncio.gather`` for
  parallelism. #11 is stdlib-only SYNC core; #7's real Embedder is async+numpy
  and is NOT materialized here. #11 therefore calls kNN and BM25 SEQUENTIALLY.
  This is an HONEST deviation/deferral: ``asyncio.gather`` is an MVP-1 micro-opt
  deferred until #7 lands. ADR-10 is preserved because RRF is rank-based and the
  ``(-score, ep_id)`` tie-break is independent of the arrival order of the two
  sources — two runs with the same state yield the same fused list regardless
  of call order. The ~30ms stage-1 budget claim is a #6/#16 concern, not #11's
  control flow.

- **Stage 2** (anchor-driven, optional, hops=1): the top-1 anchor of stage 1
  (or an explicit ``anchor_ep_id``) drives the chain (``#2.chain_from``) and,
  only when ``bfs_as_index_enabled`` (mediano, pending #8/#10 sign-off), the BFS
  (``#10`` via ``EpisodeIndexRepository.bfs_neighbors_state_at``, SO-8b). Cap is
  ONE anchor + hops=1 to fit the 250ms budget; if stage 2 would risk it, the
  mediano flag stays False and stage 2 is chain-only (BFS stays TIMELINE-only).

- **Fusion:** ``rrf_fuse`` over the union of stage 1 + stage 2.

Ownership (f5-11 §3, load-bearing): #11 OWNS fusion + final ranking +
``FusedCandidate`` (the type lives in ``contracts.retrieval``). #6 returns raw
hits per source (NO fusion, NO cross-source ranking). #8 PROJECTS, it does not
re-rank (``score`` is passthrough). #10 supplies the BFS axis; #2 supplies the
chain (read-only). #11 never touches storage directly — every source is invoked
via the typed Protocol of its owning component.

Spec-inconsistency resolutions (documented for review; see plan §"Inconsistencias"):
- ``graph_repo`` is typed ``EpisodeIndexRepository | None`` and #11 calls
  ``bfs_neighbors_state_at`` (SO-8b, the SIGNED method), NOT the forward-declared
  ``#10.GraphRepository.neighbors_state_at``. When #10 ships, it widens this
  additively. (spec §4.1/§7.7 use the #10 forward-declared name.)
- ``cognitive_type`` for G1 client-side filtering is read via
  ``episode_repo.get(ep_id).cognitive_type`` (matches spec §7.7 pseudocode). The
  cheaper no-body read from ``episode_index`` (spec §4.3) is a mediano ADDITIVE
  (``index_repo`` param), not in this standalone signature.
- The chain-PIT predicates follow the disclosure conftest ``_pit_ok`` (ADR-03-
  compliant: state_at = valid_time axis ONLY; known_at = transaction_time axis
  ONLY), NOT spec §7.5 which mixes axes (it includes ``expired_at`` in the
  state_at predicate — an ADR-03 violation).
- ``HopsCapExceeded`` is handled by ``safe_hops = min(hops, MAX_HOPS_MVP1)``
  BEFORE the call. The spec §7.7 try/except retry is UNREACHABLE dead code (the
  cap prevents the raise); it is DROPPED. If the repo raises ``HopsCapExceeded``
  despite the cap (a #6/#10 bug), it propagates (fail-loud, ADR-10).
- ``pit=None`` uses an injectable ``clock`` for ``now`` (ADR-10 reproducibility),
  NOT ``datetime.now(UTC)`` inline.

References:
- f5-11 §4 (sources), §7 (PIT), §7.7 (recall pseudocode), §11 (degradation)
- f6-signoffs.md SO-6 (RRF in Python puro en #11)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.contracts.engine import Episode, EpisodeRepository
from seahorse.contracts.index import (
    MAX_HOPS_MVP1,
    PIT_KIND_VALUES,
    IndexRowData,
    PITKind,
)
from seahorse.contracts.persistence import (
    EpisodeIndexRepository,
    FullTextHit,
    FullTextIndexRepository,
    VectorHit,
    VectorIndexRepository,
)
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import TOP_K, PITPoint
from seahorse.retrieval.errors import BfsKnownAtUnsupported, RetrievalInvalidPITKind
from seahorse.retrieval.fusion import SourceList, rrf_fuse
from seahorse.retrieval.recency import RecencyConfig, apply_recency_boost

# A hit from either source axis. ``_filter_hits_by_cognitive_type`` is generic so
# it preserves the concrete hit type (``list[VectorHit]`` / ``list[FullTextHit]``)
# instead of widening to ``list[VectorHit | FullTextHit]`` (mypy-safe reassignment).
_HitT = TypeVar("_HitT", VectorHit, FullTextHit)

_logger = logging.getLogger("seahorse.retrieval.engine")


def _default_clock() -> datetime:
    return datetime.now(UTC)


def recall(
    query: str,
    *,
    pit: PITPoint | None,
    embedder: QueryEmbedder,
    vector_repo: VectorIndexRepository,
    fts_repo: FullTextIndexRepository,
    episode_repo: EpisodeRepository,
    graph_repo: EpisodeIndexRepository | None = None,
    index_repo: EpisodeIndexRepository | None = None,
    k: int = TOP_K,
    cognitive_type: str | None = None,
    subject_filter: str | None = None,
    anchor_ep_id: str | None = None,
    hops: int = 1,
    bfs_as_index_enabled: bool = False,
    bfs_known_at_supported: bool = False,
    clock: Callable[[], datetime] | None = None,
    recency: RecencyConfig | None = None,
) -> list[FusedCandidate]:
    """Hybrid retrieval entrypoint — 2-stage RRF fusion (ADR-10, ADR-03).

    Stage 1 = kNN + BM25 (query-driven). Stage 2 = chain [+ BFS if mediano] over
    the stage-1 top-1 anchor (or ``anchor_ep_id``), hops=1. RRF over the union.
    The two bi-temporal axes NEVER mix within one recall (ADR-03): ``pit.kind``
    is validated ONCE and the same kind fans to ALL sources.

    Robust to partial/empty source lists (f5-11 §11): RRF fuses whatever each
    source returned; it never pads with invented scores (ADR-10). If stage 1 is
    empty AND no ``anchor_ep_id`` is given, stage 2 is skipped (no anchor).

    F1 recency (cerebras-f-feasibility §3): when ``recency`` is passed AND
    ``pit is None``, the fused list gets a bounded exponential recency boost
    folded into ``FusedCandidate.score`` (``index_repo.get_rows`` batch-reads
    ``created_at`` — one ``IN`` query for ≤k candidates, no N+1). Default-OFF:
    ``recency=None`` keeps the pure-RRF bit-comparable fingerprint (ADR-10);
    PIT queries (``state_at``/``known_at``) reproduce state as-of-``t`` with pure
    RRF and are NEVER boosted.
    """
    _validate_pit(pit)
    now = clock() if clock is not None else _default_clock()

    # --- Stage 1: query-driven kNN + BM25 -------------------------------
    query_vec = embedder.embed_query(query)
    vec_hits = _knn(query_vec, k, pit, cognitive_type, vector_repo, episode_repo)
    fts_hits = _bm25(query, k, pit, subject_filter, cognitive_type, fts_repo, episode_repo)

    stage1 = rrf_fuse(
        [
            SourceList("vector", vec_hits, _hit_ep_id),
            SourceList("bm25", fts_hits, _hit_ep_id),
        ],
        k=k,
    )

    # --- Stage 2: anchor-driven chain [+ BFS if mediano] ----------------
    chain_eps: list[Episode] = []
    bfs_rows: list[IndexRowData] = []
    anchor = anchor_ep_id or (stage1[0].ep_id if stage1 else None)
    if anchor is not None:
        chain = episode_repo.chain_from(anchor)
        chain_eps = _project_chain(chain, pit, cognitive_type, now)
        if bfs_as_index_enabled and graph_repo is not None:
            try:
                bfs_rows = _bfs(
                    graph_repo,
                    anchor,
                    pit,
                    hops,
                    cognitive_type,
                    bfs_known_at_supported,
                    now,
                )
            except BfsKnownAtUnsupported:
                # known_at BFS blocked on TD-2: drop the BFS axis, keep vector+bm25+chain.
                bfs_rows = []

    # --- Fusion over the union of stage 1 + stage 2 ---------------------
    fused = rrf_fuse(
        [
            SourceList("vector", vec_hits, _hit_ep_id),
            SourceList("bm25", fts_hits, _hit_ep_id),
            SourceList("chain", chain_eps, _episode_id),
            SourceList("bfs", bfs_rows, _row_ep_id),
        ],
        k=k,
    )
    # F1 recency (default-OFF, ADR-10): boost ONLY in the "now" regime (pit is
    # None); PIT queries reproduce state as-of-t with pure RRF. The boost is
    # folded into FusedCandidate.score (never an external reorder) so #8's
    # IndexRow.score passthrough stays truthful. Requires index_repo to batch-
    # read created_at; without it the boost is skipped (honest, never invented).
    if recency is not None and pit is None:
        if index_repo is None:
            # Honest skip (ADR-10): recency requested but no index_repo to batch-
            # read created_at — the boost is never invented. Log for observability.
            _logger.warning(
                "recency requested but index_repo is None; boost skipped (pure RRF)"
            )
        else:
            try:
                created_at = _read_created_at_batch(index_repo, [c.ep_id for c in fused])
                fused = apply_recency_boost(fused, created_at, now, recency, k=k)
            except Exception:  # noqa: BLE001 — a failure in the OPTIONAL recency
                # signal must not kill the whole ranking (which would degrade the
                # hybrid path to G2). Keep the pure-RRF result (ADR-10 honest).
                _logger.warning(
                    "recency boost failed; keeping pure RRF", exc_info=True
                )
    return fused


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit_ep_id(hit: VectorHit | FullTextHit) -> str:
    return hit.ep_id


def _episode_id(episode: Episode) -> str:
    return episode.id


def _row_ep_id(row: IndexRowData) -> str:
    return row.ep_id


def _read_created_at_batch(
    index_repo: EpisodeIndexRepository, ep_ids: Sequence[str]
) -> dict[str, datetime]:
    """Batch-read ``created_at`` for ≤k candidates (one ``IN`` query, no N+1).

    F1 recency (cerebras-f-feasibility §3.3): ``index_repo.get_rows`` is a single
    ``IN`` query over the fused candidate ids. Rows missing from the result are
    simply absent from the map — ``apply_recency_boost`` leaves them unboosted.
    """
    rows = index_repo.get_rows(list(ep_ids))
    return {r.ep_id: r.created_at for r in rows}


def _validate_pit(pit: PITPoint | None) -> None:
    """Validate ``pit.kind`` ONCE, before fan-out (ADR-03: axes never mix)."""
    if pit is not None and pit.kind not in PIT_KIND_VALUES:
        raise RetrievalInvalidPITKind(pit.kind)


def _knn(
    query_vec: Any,
    k: int,
    pit: PITPoint | None,
    cognitive_type: str | None,
    vector_repo: VectorIndexRepository,
    episode_repo: EpisodeRepository,
) -> list[VectorHit]:
    """Route kNN by pit. ``cognitive_type`` is push-down for vigent knn (the ONLY
    method with ``cognitive_types``); client-side for the PIT variants (G1)."""
    if pit is None:
        return vector_repo.knn(
            query_vec,
            k,
            vigent_only=True,
            cognitive_types=[cognitive_type] if cognitive_type else None,
        )
    if pit.kind == "state_at":
        hits = vector_repo.knn_state_at(query_vec, k, pit.t)
    elif pit.kind == "known_at":
        hits = vector_repo.knn_known_at(query_vec, k, pit.t)
    else:  # _validate_pit already rejected unknown kinds (ADR-03); defensive
        raise RetrievalInvalidPITKind(pit.kind)
    if cognitive_type:  # G1: client-side (PIT knn has no cognitive_types)
        hits = _filter_hits_by_cognitive_type(hits, cognitive_type, episode_repo)
    return hits


def _bm25(
    query: str,
    k: int,
    pit: PITPoint | None,
    subject_filter: str | None,
    cognitive_type: str | None,
    fts_repo: FullTextIndexRepository,
    episode_repo: EpisodeRepository,
) -> list[FullTextHit]:
    """Route BM25 by pit. ``subject_filter`` ONLY for vigent search (PIT variants
    do not accept it — mediano). ``cognitive_type`` is ALWAYS client-side (G1):
    no BM25 method (vigent or PIT) exposes ``cognitive_types``."""
    if pit is None:
        hits = fts_repo.search(query, k, vigent_only=True, subject_filter=subject_filter)
    elif pit.kind == "state_at":
        hits = fts_repo.search_state_at(query, k, pit.t)
    elif pit.kind == "known_at":
        hits = fts_repo.search_known_at(query, k, pit.t)
    else:  # _validate_pit already rejected unknown kinds (ADR-03); defensive
        raise RetrievalInvalidPITKind(pit.kind)
    if cognitive_type:  # G1: client-side for ALL BM25
        hits = _filter_hits_by_cognitive_type(hits, cognitive_type, episode_repo)
    return hits


def _filter_hits_by_cognitive_type(
    hits: Sequence[_HitT],
    cognitive_type: str,
    episode_repo: EpisodeRepository,
) -> list[_HitT]:
    """G1 client-side filter. #6 ``search`` and ALL PIT variants lack
    ``cognitive_types`` (only vigent ``knn`` has it). Robust to ``< k`` after
    filtering: returns what matches, NO padding (ADR-10).

    Reads ``cognitive_type`` via ``episode_repo.get`` (matches f5-11 §7.7
    pseudocode). The cheaper no-body read from ``episode_index`` (§4.3) is a
    mediano ADDITIVE (an ``index_repo`` param), not in this standalone signature.
    """
    out: list[_HitT] = []
    for h in hits:
        ep = episode_repo.get(h.ep_id)
        if ep is not None and ep.cognitive_type == cognitive_type:
            out.append(h)
    return out


def _project_chain(
    chain: Sequence[Episode],
    pit: PITPoint | None,
    cognitive_type: str | None,
    now: datetime,
) -> list[Episode]:
    """Read-only PIT projection of the ``supersedes`` chain (f5-11 §7.5).

    ``chain_from`` returns the WHOLE chain ordered by ``created_at`` asc and
    takes NO ``t``; #11 projects it read-only under PIT (it never mutates the
    chain — ``EpisodeRepository`` exposes no chain mutators). Predicates follow
    the disclosure conftest ``_pit_ok`` (ADR-03-compliant; see module docstring).
    """
    if pit is None:
        projected = _chain_active_now(chain, now)
        eps: list[Episode] = [projected] if projected is not None else []
    elif pit.kind == "state_at":
        projected = _chain_vigent_at(chain, pit.t)
        eps = [projected] if projected is not None else []
    elif pit.kind == "known_at":
        eps = _chain_known_at(chain, pit.t)
    else:  # _validate_pit already rejected unknown kinds (ADR-03); defensive
        raise RetrievalInvalidPITKind(pit.kind)
    if cognitive_type:  # G1: client-side filter over Episode.cognitive_type
        eps = [e for e in eps if e.cognitive_type == cognitive_type]
    return eps


def _chain_active_now(chain: Sequence[Episode], now: datetime) -> Episode | None:
    """Active-now projection (vigent_only=True). Combines both axes: not
    invalidated, not expired, and valid (``valid_at`` is None or ``<= now``).
    ``chain`` is ordered by ``created_at`` asc; the LAST active is the current
    active version. Predicates cited verbatim from the disclosure conftest."""
    candidates = [
        e
        for e in chain
        if e.invalid_at is None
        and e.expired_at is None
        and (e.valid_at is None or e.valid_at <= now)
    ]
    return candidates[-1] if candidates else None


def _chain_vigent_at(chain: Sequence[Episode], t: datetime) -> Episode | None:
    """``state_at`` projection. ADR-03: valid_time axis ONLY (``valid_at``/
    ``invalid_at``); ``expired_at`` (transaction_time) is NOT considered. The
    LAST vigent-at-``t`` is the active version at ``t`` (chain is
    ``created_at``-ascending). CC-2 (C8.6): predicate (disclosure conftest
    ``_pit_ok``): ``valid_at is None or valid_at <= t`` ("from forever" is valid
    at any ``t``, f5-02 §2 line 85) ``and (invalid_at is None or invalid_at > t)``."""
    candidates = [
        e
        for e in chain
        if (e.valid_at is None or e.valid_at <= t) and (e.invalid_at is None or e.invalid_at > t)
    ]
    return candidates[-1] if candidates else None


def _chain_known_at(chain: Sequence[Episode], t: datetime) -> list[Episode]:
    """``known_at`` projection. ADR-03: transaction_time axis ONLY
    (``created_at``/``expired_at``); ``valid_at``/``invalid_at`` are NOT
    considered. Returns ALL episodes the system knew at ``t`` (chain is
    ``created_at``-ascending). Predicate (disclosure conftest ``_pit_ok``):
    ``created_at <= t and (expired_at is None or expired_at > t)``."""
    return [e for e in chain if e.created_at <= t and (e.expired_at is None or e.expired_at > t)]


def _bfs(
    graph_repo: EpisodeIndexRepository,
    anchor: str,
    pit: PITPoint | None,
    hops: int,
    cognitive_type: str | None,
    bfs_known_at_supported: bool,
    now: datetime,
) -> list[IndexRowData]:
    """BFS-as-INDEX mediano extension (f5-11 §4.1/§7.6). Uses the SIGNED SO-8b
    method ``EpisodeIndexRepository.bfs_neighbors_state_at`` (not the #10
    forward-declared name). ``pit=None`` resolves to ``state_at`` at the
    injected ``now``. ``known_at`` without TD-2 sign-off raises
    ``BfsKnownAtUnsupported`` (NO silent ``state_at`` fallback — ADR-03). Hops
    are capped to ``MAX_HOPS_MVP1`` BEFORE the call; no dead try/except retry."""
    if pit is None:
        t_bfs = now
        kind_bfs: PITKind = "state_at"
    else:
        t_bfs = pit.t
        kind_bfs = pit.kind
        if kind_bfs == "known_at" and not bfs_known_at_supported:
            raise BfsKnownAtUnsupported()
    safe_hops = min(hops, MAX_HOPS_MVP1)
    rows = graph_repo.bfs_neighbors_state_at(
        anchor, t_bfs, pit_kind=kind_bfs, hops=safe_hops, include_tags_soft=False
    )
    if cognitive_type:  # G1: client-side filter over IndexRowData.cognitive_type
        rows = [r for r in rows if r.cognitive_type == cognitive_type]
    return rows


__all__ = ["recall"]
