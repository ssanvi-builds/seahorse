"""Hybrid Retrieval — the ``recall`` entrypoint + 2-stage orchestration.

``recall`` takes a ``query`` and optional ``PITPoint`` / ``cognitive_type`` /
``anchor_ep_id``, invokes the query-driven sources (kNN + BM25), optionally
expands with the anchor-driven stage-2 source (the ``supersedes`` chain), and
fuses everything with ``rrf_fuse`` producing ``list[FusedCandidate]`` (no body)
— the extension point the disclosure layer's ``materialize_index`` consumes.

2-STAGE model:

- **Stage 1** (query-driven): kNN + BM25. Both are query-based, so they do NOT
  need an anchor. This module is stdlib-only SYNC core; the real async embedder
  is not materialized here, so kNN and BM25 are called SEQUENTIALLY rather than
  concurrently. This is an honest deferral: parallelism is a micro-optimization
  deferred to a later release. Reproducibility is preserved because RRF is
  rank-based and the ``(-score, ep_id)`` tie-break is independent of the arrival
  order of the two sources — two runs with the same state yield the same fused
  list regardless of call order. The ~30ms stage-1 budget is a benchmark
  concern, not part of this module's control flow.

- **Stage 2** (anchor-driven, optional): the top-1 anchor of stage 1 (or an
  explicit ``anchor_ep_id``) drives the chain (``chain_from``). The BFS-as-index
  axis was removed (unreachable dead code; the F7 (e) multi-hop experiment
  recommends a physical graph with typed edge traversal — a different construct
  — and the ``graph_bfs`` timeline axis already covers the user-facing graph
  traversal).

- **Fusion:** ``rrf_fuse`` over the union of stage 1 + stage 2.

Ownership: this module owns fusion + final ranking + ``FusedCandidate`` (the
type lives in ``contracts.retrieval``). The source repositories return raw hits
per source (NO fusion, NO cross-source ranking). The disclosure layer projects;
it does not re-rank (``score`` is passthrough). The BFS axis and the chain
supply their read-only sources. This module never touches storage directly —
every source is invoked via the typed Protocol of its owning component.

Implementation notes:
- ``graph_repo`` is typed ``EpisodeIndexRepository | None`` and this module calls
  ``bfs_neighbors_state_at`` (the signed method), not a forward-declared name;
  when the BFS axis ships it widens this additively.
- ``cognitive_type`` for client-side filtering is read via
  ``episode_repo.get(ep_id).cognitive_type``. The cheaper no-body read from
  ``episode_index`` is a medium-term addition (an ``index_repo`` param), not in
  this standalone signature.
- The chain-PIT predicates follow the disclosure layer's ``_pit_ok`` helper
  (state_at = valid-time axis ONLY; known_at = transaction-time axis ONLY), so
  the two bi-temporal axes never mix within one predicate.
- ``HopsCapExceeded`` is handled by ``safe_hops = min(hops, MAX_HOPS_MVP1)``
  BEFORE the call. If the repository raises despite the cap (a source bug), it
  propagates (fail-loud).
- ``pit=None`` uses an injectable ``clock`` for ``now`` (reproducibility), NOT
  ``datetime.now(UTC)`` inline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.contracts.engine import Episode, EpisodeRepository
from seahorse.contracts.index import (
    PIT_KIND_VALUES,
    IndexRowData,
)
from seahorse.contracts.persistence import (
    EpisodeIndexRepository,
    FullTextHit,
    FullTextIndexRepository,
    VectorHit,
    VectorIndexRepository,
)
from seahorse.contracts.rerank import QueryReranker
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import TOP_K, PITPoint
from seahorse.retrieval.constants import RERANK_OVERFETCH_K
from seahorse.retrieval.decay import DecayConfig, apply_decay_bias
from seahorse.retrieval.errors import RetrievalInvalidPITKind
from seahorse.retrieval.fusion import SourceList, rrf_fuse
from seahorse.retrieval.recency import RecencyConfig, apply_recency_boost
from seahorse.retrieval.rerank import apply_rerank

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
    index_repo: EpisodeIndexRepository | None = None,
    k: int = TOP_K,
    cognitive_type: str | None = None,
    subject_filter: str | None = None,
    anchor_ep_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
    recency: RecencyConfig | None = None,
    decay: DecayConfig | None = None,
    reranker: QueryReranker | None = None,
    k_rerank: int = RERANK_OVERFETCH_K,
    rrf_k: int | None = None,
    rerank_text: str = "summary",
) -> list[FusedCandidate]:
    """Hybrid retrieval entrypoint — 2-stage RRF fusion + optional stage-3 rerank.

    Stage 1 = kNN + BM25 (query-driven). Stage 2 = chain over the stage-1 top-1
    anchor (or ``anchor_ep_id``). RRF over the union.
    The two bi-temporal axes NEVER mix within one recall: ``pit.kind`` is
    validated ONCE and the same kind fans to ALL sources.

    Robust to partial/empty source lists: RRF fuses whatever each source
    returned; it never pads with invented scores. If stage 1 is empty AND no
    ``anchor_ep_id`` is given, stage 2 is skipped (no anchor).

    Recency: when ``recency`` is passed AND ``pit is None``, the fused list gets
    a bounded exponential recency boost folded into ``FusedCandidate.score``
    (``index_repo.get_rows`` batch-reads ``created_at`` — one ``IN`` query for
    ≤k candidates, no N+1). Default-OFF: ``recency=None`` keeps the pure-RRF
    bit-comparable fingerprint; PIT queries (``state_at``/``known_at``) reproduce
    state as-of-``t`` with pure RRF and are NEVER boosted.

    Decay (Sprint D): when ``decay`` is passed AND ``pit is None``, the fused
    list gets an Ebbinghaus forgetting-curve downweight folded into
    ``FusedCandidate.score`` (``score · 2^(-age_days/half_life[type])``, factor
    in ``(0, 1]``). ``index_repo.get_rows`` batch-reads ``created_at`` +
    ``cognitive_type`` (one ``IN`` query, no N+1). Default-OFF:
    ``decay=None`` keeps the pure-RRF bit-comparable fingerprint; PIT queries
    reproduce state as-of-``t`` with pure RRF and are NEVER decayed. No writes
    (R2): the read path never writes; ``expired_at`` stays NULL. When both
    ``recency`` and ``decay`` are set, recency folds first, then decay
    (multiplicative compound, deterministic).

    Rerank: when ``reranker`` is passed, the RRF fusion over-fetches to
    ``k_rerank`` (NOT ``k``), the candidates are hydrated with summary/subject
    (``index_repo.get_rows`` — NOT ``body_md``), each (query, text) pair is
    scored by the cross-encoder, and the list is reordered + truncated to ``k``.
    The cross-encoder score REPLACES the RRF score (the manifest records
    ``score_source: "rrf_rerank"``). Default-OFF: ``reranker=None`` keeps the
    pure-RRF bit-comparable fingerprint. Honest degrade: a missing ``index_repo``
    (no text to hydrate) or a reranker failure keeps the RRF order truncated to
    ``k`` — never invented scores.
    """
    _validate_pit(pit)
    now = clock() if clock is not None else _default_clock()
    # The RRF fusion over-fetches to k_rerank so the cross-encoder has ~20
    # candidates to reorder before truncating to k.
    k_fuse = k_rerank if reranker is not None else k

    # --- Stage 1: query-driven kNN + BM25 -------------------------------
    query_vec = embedder.embed_query(query)
    vec_hits = _knn(
        query_vec, k_fuse, pit, cognitive_type, vector_repo, episode_repo, index_repo
    )
    fts_hits = _bm25(
        query, k_fuse, pit, subject_filter, cognitive_type, fts_repo, episode_repo, index_repo
    )

    stage1 = rrf_fuse(
        [
            SourceList("vector", vec_hits, _hit_ep_id),
            SourceList("bm25", fts_hits, _hit_ep_id),
        ],
        k=k_fuse,
        rrf_k=rrf_k,
    )

    # --- Stage 2: anchor-driven chain ------------------------------------
    chain_eps: list[Episode] = []
    anchor = anchor_ep_id or (stage1[0].ep_id if stage1 else None)
    if anchor is not None:
        chain = episode_repo.chain_from(anchor)
        chain_eps = _project_chain(chain, pit, cognitive_type, now)

    # --- Fusion over the union of stage 1 + stage 2 ---------------------
    fused = rrf_fuse(
        [
            SourceList("vector", vec_hits, _hit_ep_id),
            SourceList("bm25", fts_hits, _hit_ep_id),
            SourceList("chain", chain_eps, _episode_id),
        ],
        k=k_fuse,
        rrf_k=rrf_k,
    )
    # Recency (default-OFF): boost ONLY in the "now" regime (pit is None); PIT
    # queries reproduce state as-of-t with pure RRF. The boost is folded into
    # FusedCandidate.score (never an external reorder) so the disclosure layer's
    # IndexRow.score passthrough stays truthful. Requires index_repo to batch-
    # read created_at; without it the boost is skipped (honest, never invented).
    # When rerank is enabled, the boost keeps k_rerank candidates (k=k_fuse) so
    # the cross-encoder still has the full over-fetch set to reorder.
    if recency is not None and pit is None:
        if index_repo is None:
            # Honest skip: recency requested but no index_repo to batch-read
            # created_at — the boost is never invented. Log for observability.
            _logger.warning(
                "recency requested but index_repo is None; boost skipped (pure RRF)"
            )
        else:
            try:
                created_at = _read_created_at_batch(index_repo, [c.ep_id for c in fused])
                fused = apply_recency_boost(fused, created_at, now, recency, k=k_fuse)
            except Exception:  # noqa: BLE001 — a failure in the OPTIONAL recency
                # signal must not kill the whole ranking (which would degrade the
                # hybrid path to the listing regime). Keep the pure-RRF result.
                _logger.warning(
                    "recency boost failed; keeping pure RRF", exc_info=True
                )
    # Decay (Sprint D, default-OFF): the Ebbinghaus forgetting-curve downweight
    # folds into FusedCandidate.score ONLY in the "now" regime (pit is None);
    # PIT queries reproduce state as-of-t with pure RRF. Reads created_at +
    # cognitive_type in batch via index_repo.get_rows (one IN query, no N+1).
    # No writes (R2): expired_at stays NULL. When recency is also set, recency
    # folds first, then decay (multiplicative compound, deterministic).
    if decay is not None and pit is None:
        if index_repo is None:
            # Honest skip: decay requested but no index_repo to batch-read
            # created_at/cognitive_type — the bias is never invented.
            _logger.warning(
                "decay requested but index_repo is None; bias skipped (pure RRF)"
            )
        else:
            try:
                created_at, cognitive_type_by_ep_id = _read_decay_batch(
                    index_repo, [c.ep_id for c in fused]
                )
                fused = apply_decay_bias(
                    fused,
                    created_at,
                    cognitive_type_by_ep_id,
                    now,
                    decay,
                    k=k_fuse,
                )
            except Exception:  # noqa: BLE001 — a failure in the OPTIONAL decay
                # signal must not kill the whole ranking (which would degrade the
                # hybrid path to the listing regime). Keep the current result.
                _logger.warning(
                    "decay bias failed; keeping current ranking", exc_info=True
                )
    # Rerank (default-OFF): the cross-encoder reorders the fused candidates by
    # relevance to the query, replacing the RRF score (the manifest records
    # score_source="rrf_rerank"). Text = summary/subject via index_repo.get_rows
    # (NOT body_md). Honest degrade: no index_repo or a reranker failure keeps
    # the RRF order truncated to k.
    if reranker is not None:
        try:
            if rerank_text == "body":
                # A6 re-test: score the FULL body (the answer often sits mid-turn,
                # not in the ~200-char summary/subject). Per-episode ``get`` is
                # N+1 — acceptable for the synthetic measurement; a production
                # body-rerank would batch-read via a dedicated method.
                text_by_ep = {}
                for c in fused:
                    ep = episode_repo.get(c.ep_id)
                    if ep is not None:
                        text_by_ep[c.ep_id] = ep.body or ""
            elif index_repo is None:
                _logger.warning(
                    "rerank requested but index_repo is None; keeping RRF order (k)"
                )
                fused = fused[:k]
                return fused
            else:
                rows = index_repo.get_rows([c.ep_id for c in fused])
                text_by_ep = {r.ep_id: _rerank_text(r) for r in rows}
            docs = [text_by_ep.get(c.ep_id, "") for c in fused]
            scores = reranker.rerank(query, docs)
            fused = apply_rerank(fused, scores, k=k)
        except Exception:  # noqa: BLE001 — a failure in the OPTIONAL rerank
            # must not kill the whole ranking (which would degrade the hybrid
            # path to the listing regime). Keep the RRF order truncated to k.
            _logger.warning(
                "rerank failed; keeping RRF order", exc_info=True
            )
            fused = fused[:k]
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


def _rerank_text(row: IndexRowData) -> str:
    """The text the cross-encoder scores: summary or subject (NOT body_md).

    ``FusedCandidate`` is body-less; the stage-3 hydrates the searchable text
    via ``index_repo.get_rows`` (summary/subject, ~20×200 chars). The body is
    deliberately NOT read — a later measurement pass decides whether the body
    adds signal before paying its cost.
    """
    return row.summary or row.subject or ""


def _read_created_at_batch(
    index_repo: EpisodeIndexRepository, ep_ids: Sequence[str]
) -> dict[str, datetime]:
    """Batch-read ``created_at`` for ≤k candidates (one ``IN`` query, no N+1).

    ``index_repo.get_rows`` is a single ``IN`` query over the fused candidate
    ids. Rows missing from the result are simply absent from the map —
    ``apply_recency_boost`` leaves them unboosted.
    """
    rows = index_repo.get_rows(list(ep_ids))
    return {r.ep_id: r.created_at for r in rows}


def _read_decay_batch(
    index_repo: EpisodeIndexRepository, ep_ids: Sequence[str]
) -> tuple[dict[str, datetime], dict[str, str]]:
    """Batch-read ``created_at`` + ``cognitive_type`` for ≤k candidates.

    One ``IN`` query (``index_repo.get_rows``) feeds both maps — the D3 fix, no
    N+1. Rows missing from the result are simply absent from the maps —
    ``apply_decay_bias`` leaves them undecayed.
    """
    rows = index_repo.get_rows(list(ep_ids))
    created_at = {r.ep_id: r.created_at for r in rows}
    cognitive_type = {r.ep_id: r.cognitive_type for r in rows}
    return created_at, cognitive_type


def _validate_pit(pit: PITPoint | None) -> None:
    """Validate ``pit.kind`` ONCE, before fan-out (the bi-temporal axes never mix)."""
    if pit is not None and pit.kind not in PIT_KIND_VALUES:
        raise RetrievalInvalidPITKind(pit.kind)


def _knn(
    query_vec: Any,
    k: int,
    pit: PITPoint | None,
    cognitive_type: str | None,
    vector_repo: VectorIndexRepository,
    episode_repo: EpisodeRepository,
    index_repo: EpisodeIndexRepository | None = None,
) -> list[VectorHit]:
    """Route kNN by pit. ``cognitive_type`` is push-down for current-state knn
    (the ONLY method with ``cognitive_types``); client-side for the PIT variants
    (via ``index_repo`` when available — one IN query, no body)."""
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
    else:  # _validate_pit already rejected unknown kinds; defensive
        raise RetrievalInvalidPITKind(pit.kind)
    if cognitive_type:  # client-side (PIT knn has no cognitive_types)
        hits = _filter_hits_by_cognitive_type(
            hits, cognitive_type, episode_repo, index_repo
        )
    return hits


def _bm25(
    query: str,
    k: int,
    pit: PITPoint | None,
    subject_filter: str | None,
    cognitive_type: str | None,
    fts_repo: FullTextIndexRepository,
    episode_repo: EpisodeRepository,
    index_repo: EpisodeIndexRepository | None = None,
) -> list[FullTextHit]:
    """Route BM25 by pit. ``subject_filter`` ONLY for current-state search (PIT
    variants do not accept it — a medium-term extension). ``cognitive_type`` is
    ALWAYS client-side: no BM25 method (current-state or PIT) exposes
    ``cognitive_types`` (filtered via ``index_repo`` when available — one IN
    query, no body)."""
    if pit is None:
        hits = fts_repo.search(query, k, vigent_only=True, subject_filter=subject_filter)
    elif pit.kind == "state_at":
        hits = fts_repo.search_state_at(query, k, pit.t)
    elif pit.kind == "known_at":
        hits = fts_repo.search_known_at(query, k, pit.t)
    else:  # _validate_pit already rejected unknown kinds; defensive
        raise RetrievalInvalidPITKind(pit.kind)
    if cognitive_type:  # client-side for ALL BM25
        hits = _filter_hits_by_cognitive_type(
            hits, cognitive_type, episode_repo, index_repo
        )
    return hits


def _filter_hits_by_cognitive_type(
    hits: Sequence[_HitT],
    cognitive_type: str,
    episode_repo: EpisodeRepository,
    index_repo: EpisodeIndexRepository | None = None,
) -> list[_HitT]:
    """Client-side filter. The ``search`` method and ALL PIT variants lack
    ``cognitive_types`` (only current-state ``knn`` has it). Robust to ``< k``
    after filtering: returns what matches, NO padding.

    Reads ``cognitive_type`` via ``index_repo.get_rows`` (ONE ``IN`` query, no
    body) when the index repo is available; falls back to ``episode_repo.get``
    per hit (N+1) when it is not.
    """
    if index_repo is not None:
        rows = index_repo.get_rows([h.ep_id for h in hits])
        by_id = {r.ep_id: r for r in rows}
        return [
            h
            for h in hits
            if by_id.get(h.ep_id) is not None
            and by_id[h.ep_id].cognitive_type == cognitive_type
        ]
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
    """Read-only PIT projection of the ``supersedes`` chain.

    ``chain_from`` returns the WHOLE chain ordered by ``created_at`` asc and
    takes NO ``t``; this module projects it read-only under PIT (it never mutates
    the chain — ``EpisodeRepository`` exposes no chain mutators). Predicates
    follow the disclosure layer's ``_pit_ok`` helper (the bi-temporal axes never
    mix; see module docstring).
    """
    if pit is None:
        projected = _chain_active_now(chain, now)
        eps: list[Episode] = [projected] if projected is not None else []
    elif pit.kind == "state_at":
        projected = _chain_vigent_at(chain, pit.t)
        eps = [projected] if projected is not None else []
    elif pit.kind == "known_at":
        eps = _chain_known_at(chain, pit.t)
    else:  # _validate_pit already rejected unknown kinds; defensive
        raise RetrievalInvalidPITKind(pit.kind)
    if cognitive_type:  # client-side filter over Episode.cognitive_type
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
    """``state_at`` projection. Valid-time axis ONLY (``valid_at``/``invalid_at``);
    ``expired_at`` (transaction_time) is NOT considered. The LAST
    current-state-at-``t`` is the active version at ``t`` (chain is
    ``created_at``-ascending). Predicate (disclosure layer ``_pit_ok``):
    ``valid_at is None or valid_at <= t`` ("from forever" is valid at any ``t``)
    ``and (invalid_at is None or invalid_at > t)``."""
    candidates = [
        e
        for e in chain
        if (e.valid_at is None or e.valid_at <= t) and (e.invalid_at is None or e.invalid_at > t)
    ]
    return candidates[-1] if candidates else None


def _chain_known_at(chain: Sequence[Episode], t: datetime) -> list[Episode]:
    """``known_at`` projection. Transaction-time axis ONLY (``created_at``/
    ``expired_at``); ``valid_at``/``invalid_at`` are NOT considered. Returns ALL
    episodes the system knew at ``t`` (chain is ``created_at``-ascending).
    Predicate (disclosure layer ``_pit_ok``): ``created_at <= t and (expired_at
    is None or expired_at > t)``."""
    return [e for e in chain if e.created_at <= t and (e.expired_at is None or e.expired_at > t)]




__all__ = ["recall"]
