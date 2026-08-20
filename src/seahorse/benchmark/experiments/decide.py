"""Experiment decision logic — thresholds from the experiment design.

Pure functions over ``ExperimentResult`` values; the runner feeds them the
per-variant retrieval metrics and they emit the honest recency/rerank/embedding/
decay verdict.

Thresholds:

- (a) recency: ON must NOT degrade global ``ndcg@10`` by > 1pp AND must improve
  ``recall@10`` on the ``temporal-reasoning`` / ``knowledge-update`` slices by
  ≥ 1pp (at least one). The best passing variant (largest combined slice
  improvement, then highest ndcg@10) is the calibrated recency config; if none
  passes, keep recency OFF.
- (b) rerank: the cross-encoder must improve global ``ndcg@10`` by ≥ 2pp AND
  the reranker-path INDEX p95 must be ≤ 500ms to implement reranking; otherwise
  keep RRF-only.
- (c) embed: ``body+summary`` must improve global ``recall@10`` by ≥ 1pp to
  switch the embedder to vectorial mode; otherwise keep ``body``-only.
- (d) decay (Sprint D): the Ebbinghaus bias must improve ``recall@10`` on the
  ``knowledge-update`` slice (the FAMA target: the valid new version must stay
  retrievable) by ≥ 1pp WITHOUT degrading global ``ndcg@10`` by > 1pp to go
  operational (the run-authoritative A/B); otherwise keep decay default-OFF.

Fail-loud honesty: if the baseline ran in the ``fallback_g2`` regime (hybrid
retrieval not wired), the experiment is INVALID — no decision is claimed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from seahorse.benchmark.contracts import MetricReport
from seahorse.benchmark.experiments.variants import ExperimentVariant

# 1pp = 0.01 (percent-point deltas on recall@10 / ndcg@10).
NDCG_DEGRADATION_PP = 0.01
RECALL_IMPROVEMENT_PP = 0.01

# Rerank thresholds: ndcg@10 improvement >= 2pp AND p95 <= 500ms.
NDCG_IMPROVEMENT_PP = 0.02
RERANK_P95_MS = 500.0

# The slices the recency signal must improve.
RECENCY_SLICES = ("temporal-reasoning", "knowledge-update")

# The slice the decay bias must improve: the FAMA target (old/obsolete versions
# out of the top-k, valid new versions retrievable).
DECAY_SLICES = ("knowledge-update",)

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class ExperimentResult:
    """A single variant run — retrieval metrics + honest regime detection."""

    variant: ExperimentVariant
    metrics: dict[str, MetricReport]
    detected_score_source: str
    run_errors: list[str]
    run_id: str

    def metric(self, name: str) -> MetricReport:
        if name not in self.metrics:
            raise KeyError(f"metric {name!r} not in run metrics {sorted(self.metrics)}")
        return self.metrics[name]


def _ndcg10(result: ExperimentResult) -> float:
    return result.metric("ndcg@10").global_value


def _recall10_by_slice(result: ExperimentResult) -> dict[str, float]:
    return result.metric("recall@10").by_slice


def _recall10_global(result: ExperimentResult) -> float:
    return result.metric("recall@10").global_value


def decide_recency(
    baseline: ExperimentResult, variants: Sequence[ExperimentResult]
) -> dict:
    """Apply the recency thresholds and pick the calibrated config, or keep recency OFF.

    Returns a decision dict (``decision``, ``flip``, ``variant``, ``reason``,
    ``passing``). If the baseline is ``fallback_g2`` the hybrid regime was NOT
    wired and no decision is claimed (``invalid_regime``, fail-loud honesty).
    """
    if baseline.detected_score_source == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "variant": None,
            "reason": (
                "baseline ran in the listing regime (hybrid retrieval not wired); "
                "the recency comparison is not meaningful — re-run with the embeddings extra"
            ),
            "passing": [],
        }
    base_ndcg = _ndcg10(baseline)
    base_slices = _recall10_by_slice(baseline)
    passing: list[ExperimentResult] = []
    for v in variants:
        if _ndcg10(v) < base_ndcg - NDCG_DEGRADATION_PP:
            continue  # ON degrades global ndcg@10 by > 1pp → rejected
        v_slices = _recall10_by_slice(v)
        improvements = [
            v_slices.get(s, base_slices.get(s, 0.0)) - base_slices.get(s, 0.0)
            for s in RECENCY_SLICES
            if base_slices.get(s) is not None and v_slices.get(s) is not None
        ]
        if improvements and max(improvements) >= RECALL_IMPROVEMENT_PP:
            passing.append(v)
    if not passing:
        return {
            "decision": "keep_off",
            "flip": False,
            "variant": None,
            "reason": (
                f"no recency config improved recall@10 on {RECENCY_SLICES} by "
                f">= {RECALL_IMPROVEMENT_PP:.0%} without degrading global ndcg@10 "
                f"by > {NDCG_DEGRADATION_PP:.0%}; recency stays default-OFF"
            ),
            "passing": [],
        }

    def _score(v: ExperimentResult) -> tuple[float, float]:
        v_slices = _recall10_by_slice(v)
        improvement = max(
            v_slices.get(s, 0.0) - base_slices.get(s, 0.0) for s in RECENCY_SLICES
        )
        return improvement, _ndcg10(v)

    best = max(passing, key=_score)
    return {
        "decision": "flip_f1",
        "flip": True,
        "variant": best.variant.name,
        "recency_config": best.variant.recency_config,
        "reason": (
            f"best calibrated config {best.variant.description!r} improves "
            f"recall@10 on {RECENCY_SLICES} >= {RECALL_IMPROVEMENT_PP:.0%} with "
            f"global ndcg@10 within {NDCG_DEGRADATION_PP:.0%} of baseline"
        ),
        "passing": [v.variant.name for v in passing],
    }


def decide_rerank(baseline: ExperimentResult, variant: ExperimentResult) -> dict:
    """Apply the rerank threshold: implement reranking iff ndcg@10 improves ≥ 2pp AND
    the rerank-path INDEX p95 ≤ 500ms.

    Returns a decision dict (``decision``, ``flip``, ``variant``, ``reason``,
    ``ndcg_delta``, ``p95_index_rerank_ms``). Invalid (no decision) when the
    baseline is ``fallback_g2`` (fail-loud honesty).
    """
    if baseline.detected_score_source == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "variant": None,
            "reason": (
                "baseline ran in the listing regime (hybrid retrieval not wired); "
                "the rerank comparison is not meaningful — re-run with the embeddings extra"
            ),
            "ndcg_delta": None,
            "p95_index_rerank_ms": None,
        }
    ndcg_delta = _ndcg10(variant) - _ndcg10(baseline)
    p95_rerank = variant.metric("latency_p95_rerank_ms").global_value
    if ndcg_delta >= NDCG_IMPROVEMENT_PP and p95_rerank <= RERANK_P95_MS:
        return {
            "decision": "implement_f2",
            "flip": True,
            "variant": variant.variant.name,
            "reason": (
                f"cross-encoder improves global ndcg@10 by {ndcg_delta:.1%} "
                f"(>= {NDCG_IMPROVEMENT_PP:.0%}) with rerank-path p95 "
                f"{p95_rerank:.0f}ms <= {RERANK_P95_MS:.0f}ms; implement reranking "
                f"(cross-encoder opt-in)"
            ),
            "ndcg_delta": ndcg_delta,
            "p95_index_rerank_ms": p95_rerank,
        }
    return {
        "decision": "keep_rrf",
        "flip": False,
        "variant": None,
        "reason": (
            f"cross-encoder ndcg@10 delta {ndcg_delta:+.1%} < "
            f"{NDCG_IMPROVEMENT_PP:.0%} OR rerank-path p95 {p95_rerank:.0f}ms > "
            f"{RERANK_P95_MS:.0f}ms; keep RRF-only (reranking not implemented)"
        ),
        "ndcg_delta": ndcg_delta,
        "p95_index_rerank_ms": p95_rerank,
    }


def decide_embed(baseline: ExperimentResult, variant: ExperimentResult) -> dict:
    """Apply the embedding threshold: switch the embed mode iff recall@10 improves ≥ 1pp.

    Returns a decision dict (``decision``, ``flip``, ``variant``, ``reason``,
    ``recall_delta``). Invalid (no decision) when the baseline is ``fallback_g2``.
    """
    if baseline.detected_score_source == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "variant": None,
            "reason": (
                "baseline ran in the listing regime (hybrid retrieval not wired); "
                "the embedding comparison is not meaningful — re-run with the embeddings extra"
            ),
            "recall_delta": None,
        }
    delta = _recall10_global(variant) - _recall10_global(baseline)
    if delta >= RECALL_IMPROVEMENT_PP:
        return {
            "decision": "flip_f3",
            "flip": True,
            "variant": variant.variant.name,
            "reason": (
                f"embed body+summary improves global recall@10 by "
                f"{delta:.1%} (>= {RECALL_IMPROVEMENT_PP:.0%}); switch the embedder "
                f"to vectorial via reindex with embed_mode='body+summary'"
            ),
            "recall_delta": delta,
        }
    return {
        "decision": "keep_body_only",
        "flip": False,
        "variant": None,
        "reason": (
            f"embed body+summary recall@10 delta {delta:+.1%} < "
            f"{RECALL_IMPROVEMENT_PP:.0%}; keep embed_mode='body' (embedder not switched)"
        ),
        "recall_delta": delta,
    }


def decide_decay_rrf(baseline: ExperimentResult, variant: ExperimentResult) -> dict:
    """Apply the decay threshold: go operational iff the Ebbinghaus bias improves
    ``recall@10`` on the ``knowledge-update`` slice (the FAMA target) by ≥ 1pp
    WITHOUT degrading global ``ndcg@10`` by > 1pp.

    Returns a decision dict (``decision``, ``flip``, ``variant``, ``reason``,
    ``recall_delta``, ``ndcg_delta``). Invalid (no decision) when the baseline
    is ``fallback_g2`` (fail-loud honesty).
    """
    if baseline.detected_score_source == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "variant": None,
            "reason": (
                "baseline ran in the listing regime (hybrid retrieval not wired); "
                "the decay comparison is not meaningful — re-run with the embeddings extra"
            ),
            "recall_delta": None,
            "ndcg_delta": None,
        }
    base_slices = _recall10_by_slice(baseline)
    v_slices = _recall10_by_slice(variant)
    recall_delta = v_slices.get("knowledge-update", 0.0) - base_slices.get(
        "knowledge-update", 0.0
    )
    ndcg_delta = _ndcg10(variant) - _ndcg10(baseline)
    if recall_delta >= RECALL_IMPROVEMENT_PP and ndcg_delta >= -NDCG_DEGRADATION_PP:
        return {
            "decision": "flip_decay",
            "flip": True,
            "variant": variant.variant.name,
            "reason": (
                f"decay improves recall@10 on {DECAY_SLICES} by "
                f"{recall_delta:.1%} (>= {RECALL_IMPROVEMENT_PP:.0%}) with global "
                f"ndcg@10 delta {ndcg_delta:+.1%} within {NDCG_DEGRADATION_PP:.0%} "
                f"of baseline; decay goes operational (default-on)"
            ),
            "recall_delta": recall_delta,
            "ndcg_delta": ndcg_delta,
        }
    return {
        "decision": "keep_off",
        "flip": False,
        "variant": None,
        "reason": (
            f"decay recall@10 delta on {DECAY_SLICES} {recall_delta:+.1%} < "
            f"{RECALL_IMPROVEMENT_PP:.0%} OR ndcg@10 delta {ndcg_delta:+.1%} "
            f"degrades beyond {NDCG_DEGRADATION_PP:.0%}; decay stays default-OFF"
        ),
        "recall_delta": recall_delta,
        "ndcg_delta": ndcg_delta,
    }


__all__ = [
    "NDCG_DEGRADATION_PP",
    "RECALL_IMPROVEMENT_PP",
    "NDCG_IMPROVEMENT_PP",
    "RERANK_P95_MS",
    "RECENCY_SLICES",
    "DECAY_SLICES",
    "ExperimentResult",
    "decide_recency",
    "decide_rerank",
    "decide_embed",
    "decide_decay_rrf",
]
