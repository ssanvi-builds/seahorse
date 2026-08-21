"""Shared real LMEB-S corpus builder for the standalone experiments.

The three standalone experiments (rrf_k, rerank_body, end_to_end) ingest the
real LMEB-S haystack and measure SESSION-level recall — any retrieved episode
from the golden session counts, because LMEB golden answers live in sessions,
not in a single identifiable turn. This module centralizes (a) the load +
optional reproducible subsample, (b) the facade + query embedder for the real
hybrid path, and (c) the haystack ingest (the exact ``SeahorseSUT.ingest``
behavior: sessions deduped by id, date-ordered oldest-first, skip extraction,
``source_type`` by temporal mode) so the three experiments don't each re-derive
it.

The subsample is the 2026-08-07 documented compromise (100 questions, fixed
seed — ``subsample.subsample_dataset``); the authoritative runs default to it
(``--no-subsample`` opts into the full-corpus overnight run).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seahorse.benchmark.contracts import BenchmarkDataset
from seahorse.benchmark.experiments.subsample import subsample_dataset
from seahorse.facade import build_facade
from seahorse.facade.types import Provenance, RememberPayload

_MIN_DT = datetime.min.replace(tzinfo=UTC)


def load_lmeb_subsample(dataset_config: str = "s", *, subsample: bool = True) -> BenchmarkDataset:
    """Load the LMEB dataset, optionally applying the reproducible subsample.

    The loader is resolved via the adapter registry (``lmeb``); the subsample
    recomputes ``split_hash`` over the SUBSAMPLED instances (honest fingerprint).
    """
    from seahorse.benchmark.adapters.registry import AdapterRegistry  # lazy
    from seahorse.benchmark.config import BenchmarkConfig  # lazy

    loader = AdapterRegistry.get("lmeb")
    dataset = loader.load(BenchmarkConfig(dataset_config=dataset_config))
    if subsample:
        dataset = subsample_dataset(dataset)
    return dataset


def build_real_facade(db_path: Path | str) -> tuple[Any, Any]:
    """The real hybrid facade over the fastembed backend (no embedder override).

    ``retrieval_available=True`` forces the hybrid wiring; the passage embedder
    auto-resolves to ``multilingual-e5-small`` (the pinned model). Returns
    ``(facade, storage)`` so the caller can ``storage.close()``.
    """
    return build_facade(db_path, retrieval_available=True)


def real_query_embedder() -> Any:
    """The sync query seam over the real fastembed backend (for ``engine.recall``).

    The standalone ``_measure`` functions call ``recall`` directly (they sweep
    ``rrf_k``/``rerank_text``/``reranker``, which the facade's ``recall`` does
    not expose); the query embedder must be the REAL one, not the synthetic hash.
    """
    from seahorse.embeddings.fastembed_backend import build_fastembed_embedder  # lazy
    from seahorse.embeddings.query_adapter import AsyncToSyncQueryEmbedder  # lazy

    return AsyncToSyncQueryEmbedder(build_fastembed_embedder())


def ingest_haystack(
    facade: Any, dataset: BenchmarkDataset, *, temporal: bool = False
) -> tuple[list[str], dict[str, str]]:
    """Ingest the dataset's haystack sessions via the facade write path (skip mode).

    Sessions are deduped by ``session_id`` across instances (the same session is
    in many questions' haystacks — re-ingesting would hit collisions) and
    ordered oldest-first (the first version of a fact before its successor).
    Returns ``(stored_ep_ids, ep_id_to_session)`` — the bridge the session-level
    recall metrics resolve retrieved ep_ids through.
    """
    sessions_by_id: dict[str, dict] = {}
    for inst in dataset.instances:
        for session in inst.haystack:
            sessions_by_id.setdefault(session["session_id"], session)
    sessions = sorted(sessions_by_id.values(), key=lambda s: s.get("date") or _MIN_DT)
    ep_ids: list[str] = []
    ep_id_to_session: dict[str, str] = {}
    for session in sessions:
        session_id = session["session_id"]
        date = session.get("date")
        for turn in session.get("turns", []):
            payload = RememberPayload(
                body=turn["body"],
                by=Provenance(
                    source_type="human" if temporal else "agent",
                    agent_id="seahorse-benchmark",
                    session_id=session_id,
                ),
                valid_at=date if temporal else None,
                title=turn.get("title"),
            )
            wr = facade.remember(payload, skip_extraction=True)
            if wr.ep_id is not None:
                ep_ids.append(wr.ep_id)
                ep_id_to_session[wr.ep_id] = session_id
    return ep_ids, ep_id_to_session


__all__ = [
    "ingest_haystack",
    "load_lmeb_subsample",
    "real_query_embedder",
]
