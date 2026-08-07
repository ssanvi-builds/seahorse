"""Bi-temporal memory metrics (f5-16 §4.5).

- ``FAMAGapMetric`` — MVP-1 SANITY CHECK: gap=0 is the expected outcome of the
  bi-temporal design (recall filters invalidated episodes by construction). It
  is NOT a competitive finding without a baseline; the discriminator is
  ``knowledge_update_accuracy``.
- ``KnowledgeUpdateAccuracyMetric`` — the MVP-1 flag metric: fraction of
  knowledge-update questions where the NEW (post-``improve``) version appears
  in top-k. Exercises the ``supersedes`` chains the ``KnowledgeUpdateSimulator``
  creates.
"""

from __future__ import annotations

from collections.abc import Sequence

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkInstance,
    MetricReport,
    MetricResult,
    SUTResponse,
)


class FAMAGapMetric:
    """FAMA-gap sanity check (f5-16 §4.5 level 1).

    ``gap = |responses with invalidated_ep_ids| / |responses|``. Since recall
    returns vigente only (``invalid_at IS NULL``), the expected value is 0.0 —
    reported with the explicit caveat that it does not measure a competitive
    gap without a baseline.
    """

    def name(self) -> str:
        return "fama_gap"

    def requires_golden(self) -> bool:
        return False

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        invalidated = sum(
            len(resp.sut_metadata.get("invalidated_ep_ids", [])) for resp in responses
        )
        n = len(responses)
        gap = invalidated / n if n else 0.0
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=gap,
                n_samples=n,
                details={
                    "caveat": "sanity_check_no_baseline_mvp1",
                    "expected": "gap=0_by_design",
                },
            ),
        )


class KnowledgeUpdateAccuracyMetric:
    """Fraction of knowledge-update questions where the NEW version is in top-k.

    The ``new_ep_ids_after_improve`` are tracked by the ``KnowledgeUpdateSimulator``
    and attached to ``inst.metadata`` by the runner (f5-16 §4.5). A question
    counts correct when at least one new ep_id appears in the retrieved set.
    """

    def name(self) -> str:
        return "knowledge_update_accuracy"

    def requires_golden(self) -> bool:
        return False

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        ku = [
            (inst, resp)
            for inst, resp in zip(instances, responses, strict=False)
            if "knowledge-update" in inst.capabilities
        ]
        correct = 0
        for inst, resp in ku:
            new_ep_ids = inst.metadata.get("new_ep_ids_after_improve", [])
            if not new_ep_ids:
                continue
            if any(ep in resp.retrieved_ep_ids for ep in new_ep_ids):
                correct += 1
        n = len(ku)
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=correct / n if n else 0.0,
                n_samples=n,
            ),
        )


__all__ = ["FAMAGapMetric", "KnowledgeUpdateAccuracyMetric"]
