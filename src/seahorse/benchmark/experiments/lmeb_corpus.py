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

from pathlib import Path
from typing import Any

from seahorse.benchmark.contracts import BenchmarkDataset
from seahorse.benchmark.experiments.subsample import subsample_dataset
from seahorse.benchmark.ingest import remember_sessions
from seahorse.facade import build_facade


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


def ingest_haystack(
    facade: Any, dataset: BenchmarkDataset, *, temporal: bool = False
) -> tuple[list[str], dict[str, str]]:
    """Ingest the dataset's haystack sessions via the facade write path (skip mode).

    The exact SeahorseSUT payload shape, shared: ``benchmark/ingest.remember_sessions``
    dedups by ``session_id`` across instances (the same session is in many
    questions' haystacks — re-ingesting would hit NOOP idempotency) and orders
    oldest-first (the first version of a fact before its successor). Returns
    ``(stored_ep_ids, ep_id_to_session)`` — the bridge the session-level recall
    metrics resolve retrieved ep_ids through.
    """
    sessions = [
        session for inst in dataset.instances for session in inst.haystack
    ]
    # remember_sessions dedups by session_id and orders oldest-first; the
    # payload shape is the single-homed ``turn_payload`` (the SUT's own).
    return remember_sessions(facade, sessions, temporal=temporal)


__all__ = [
    "ingest_haystack",
    "load_lmeb_subsample",
]
