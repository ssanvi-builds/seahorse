"""F7 experiments — the harness that decides F1 (recency), F2 (rerank), F3 (embed).

An experiment runs the #16 skeleton once per variant (``score_source`` is the
manifest variant, f7 §3) and applies the f7 §5 thresholds to the retrieval
metrics. ``run_experiment`` is the entry point; the decision functions are pure
and independently testable.

Honesty (ADR-10): ``--corpus synthetic`` (CI default) verifies the harness
MECHANICS with a deterministic embedder/reranker — it is NOT the science. The
authoritative F1/F2/F3 decision comes from ``--corpus lmeb-s`` with the real
embeddings + LongMemEval haystack.
"""

from seahorse.benchmark.experiments.decide import (
    NDCG_DEGRADATION_PP,
    NDCG_IMPROVEMENT_PP,
    RECALL_IMPROVEMENT_PP,
    RECENCY_SLICES,
    RERANK_P95_MS,
    ExperimentResult,
    decide_embed,
    decide_recency,
    decide_rerank,
)
from seahorse.benchmark.experiments.runner import (
    ExperimentReport,
    run_experiment,
)
from seahorse.benchmark.experiments.variants import (
    CORPORA,
    EXPERIMENTS,
    RERANK_OVERFETCH_K,
    ExperimentVariant,
    embed_variants,
    recency_variants,
    rerank_variants,
    variants_for,
)

__all__ = [
    "EXPERIMENTS",
    "CORPORA",
    "ExperimentVariant",
    "ExperimentResult",
    "ExperimentReport",
    "NDCG_DEGRADATION_PP",
    "RECALL_IMPROVEMENT_PP",
    "NDCG_IMPROVEMENT_PP",
    "RERANK_P95_MS",
    "RERANK_OVERFETCH_K",
    "RECENCY_SLICES",
    "recency_variants",
    "embed_variants",
    "rerank_variants",
    "variants_for",
    "decide_recency",
    "decide_rerank",
    "decide_embed",
    "run_experiment",
]
