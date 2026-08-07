"""Retrieval metrics — the LLM-free honest floor (f5-16 §4.4).

Recall@k / nDCG@k (binary) / MRR / Precision@k are computed over the
``fact_id → session_id`` bridge: each ``SUTResponse.retrieved_session_ids`` is
compared against ``golden_session_ids``. Abstention questions are excluded
(LMEB convention — no ground-truth localization). nDCG@k uses BINARY relevance
(``rel_i = 1`` if the session is golden, else 0), aligned with LongMemEval
(f5-16 §4.4 F5).

``k_effectivo`` (f5-16 §8.3 D4): when the SUT returns fewer than ``k`` results,
Precision@k uses the effective k as denominator and the metric reports it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkInstance,
    MetricReport,
    MetricResult,
    SUTResponse,
)
from seahorse.benchmark.metrics import _mean


def _slices(
    inst: BenchmarkInstance, value: float, by_type: dict, by_cog: dict
) -> None:
    by_type[inst.question_type] = by_type.get(inst.question_type, []) + [value]
    by_cog[inst.cognitive_category] = by_cog.get(inst.cognitive_category, []) + [value]


def _slice_reports(by_type: dict, by_cog: dict) -> tuple[dict[str, float], dict[str, float]]:
    return {k: _mean(v) for k, v in by_type.items()}, {k: _mean(v) for k, v in by_cog.items()}


class RecallAtK:
    """Recall@k = |golden ∩ recovered| / |golden| (denominator = |golden|)."""

    def __init__(self, k: int | None = None) -> None:
        self._k = k

    def name(self) -> str:
        return f"recall@{self._k}" if self._k else "recall@10"

    def requires_golden(self) -> bool:
        return True

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        k = self._k or config.top_k
        values: list[float] = []
        by_type: dict[str, list[float]] = {}
        by_cog: dict[str, list[float]] = {}
        for inst, resp in zip(instances, responses, strict=False):
            if inst.abstention:
                continue
            relevant = set(inst.golden_session_ids)
            if not relevant:
                continue
            recovered = set(resp.retrieved_session_ids[:k])
            recall = len(relevant & recovered) / len(relevant)
            values.append(recall)
            _slices(inst, recall, by_type, by_cog)
        by_type_r, by_cog_r = _slice_reports(by_type, by_cog)
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=_mean(values),
                by_slice=by_type_r,
                n_samples=len(values),
                details={"by_cognitive_category": by_cog_r, "k": k},
            ),
        )


class NDCGAtK:
    """nDCG@k with BINARY relevance (f5-16 §4.4 F5).

    ``DCG@k = Σ rel_i / log2(i+1)``, ``IDCG@k = Σ_{i=1..min(k,|golden|)} 1/log2(i+1)``.
    Computable under the G2 fallback (order = created_at desc) — it measures
    whether that order surfaces relevant items, not "not computable".
    """

    def __init__(self, k: int | None = None) -> None:
        self._k = k

    def name(self) -> str:
        return f"ndcg@{self._k}" if self._k else "ndcg@10"

    def requires_golden(self) -> bool:
        return True

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        k = self._k or config.top_k
        values: list[float] = []
        by_type: dict[str, list[float]] = {}
        by_cog: dict[str, list[float]] = {}
        for inst, resp in zip(instances, responses, strict=False):
            if inst.abstention:
                continue
            relevant = set(inst.golden_session_ids)
            if not relevant:
                continue
            recovered = resp.retrieved_session_ids[:k]
            dcg = sum(
                1.0 / math.log2(i + 2) for i, sid in enumerate(recovered) if sid in relevant
            )
            idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(relevant))))
            ndcg = dcg / idcg if idcg > 0 else 0.0
            values.append(ndcg)
            _slices(inst, ndcg, by_type, by_cog)
        by_type_r, by_cog_r = _slice_reports(by_type, by_cog)
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=_mean(values),
                by_slice=by_cog_r,  # ADR-10: stratified by cognitive_category
                n_samples=len(values),
                details={"by_question_type": by_type_r, "k": k, "relevance": "binary"},
            ),
        )


class MRR:
    """MRR = 1 / rank of the first relevant session (0 if none recovered)."""

    def name(self) -> str:
        return "mrr"

    def requires_golden(self) -> bool:
        return True

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        values: list[float] = []
        by_type: dict[str, list[float]] = {}
        by_cog: dict[str, list[float]] = {}
        for inst, resp in zip(instances, responses, strict=False):
            if inst.abstention:
                continue
            relevant = set(inst.golden_session_ids)
            if not relevant:
                continue
            mrr = 0.0
            for i, sid in enumerate(resp.retrieved_session_ids):
                if sid in relevant:
                    mrr = 1.0 / (i + 1)
                    break
            values.append(mrr)
            _slices(inst, mrr, by_type, by_cog)
        by_type_r, by_cog_r = _slice_reports(by_type, by_cog)
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=_mean(values),
                by_slice=by_type_r,
                n_samples=len(values),
                details={"by_cognitive_category": by_cog_r},
            ),
        )


class PrecisionAtK:
    """Precision@k = |golden ∩ recovered| / k_effectivo (f5-16 §8.3 D4).

    ``k_effectivo = min(k, len(recovered))`` — when the SUT returns fewer than
    k results, the effective k is the denominator (documented limitation, not
    an error).
    """

    def __init__(self, k: int | None = None) -> None:
        self._k = k

    def name(self) -> str:
        return f"precision@{self._k}" if self._k else "precision@10"

    def requires_golden(self) -> bool:
        return True

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        k = self._k or config.top_k
        values: list[float] = []
        k_effs: list[int] = []
        by_type: dict[str, list[float]] = {}
        by_cog: dict[str, list[float]] = {}
        for inst, resp in zip(instances, responses, strict=False):
            if inst.abstention:
                continue
            relevant = set(inst.golden_session_ids)
            recovered = resp.retrieved_session_ids[:k]
            k_eff = min(k, len(recovered))
            precision = len(relevant & set(recovered)) / k_eff if k_eff > 0 else 0.0
            values.append(precision)
            k_effs.append(k_eff)
            _slices(inst, precision, by_type, by_cog)
        by_type_r, by_cog_r = _slice_reports(by_type, by_cog)
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=_mean(values),
                by_slice=by_type_r,
                n_samples=len(values),
                details={
                    "by_cognitive_category": by_cog_r,
                    "k": k,
                    "k_effectivo_mean": _mean([float(v) for v in k_effs]),
                },
            ),
        )


__all__ = ["RecallAtK", "NDCGAtK", "MRR", "PrecisionAtK"]
