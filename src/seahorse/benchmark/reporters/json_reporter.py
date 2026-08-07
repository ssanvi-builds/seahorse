"""``JsonReporter`` — manifest.json + summary.json + samples.jsonl (f5-16 §6.4)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkDataset,
    MetricResult,
    SUTResponse,
)
from seahorse.benchmark.reporters.manifest import RunManifest, write_manifest


class JsonReporter:
    """Renders a run to the machine-readable artifacts."""

    def __init__(self, output_dir: str | Path) -> None:
        self._output_dir = Path(output_dir)

    def render(
        self,
        dataset: BenchmarkDataset,
        responses: Sequence[SUTResponse],
        metric_results: Sequence[MetricResult],
        manifest: RunManifest,
        config: BenchmarkConfig,
    ) -> str:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(manifest, self._output_dir / "manifest.json")

        summary = {r.metric_name: asdict(r.report) for r in metric_results}
        (self._output_dir / "summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8"
        )

        with open(self._output_dir / "samples.jsonl", "w", encoding="utf-8") as f:
            for i, inst in enumerate(dataset.instances):
                resp = (
                    responses[i]
                    if i < len(responses)
                    else SUTResponse(answer="", retrieved_ep_ids=(), retrieved_fact_ids=())
                )
                f.write(json.dumps(self._sample(inst, resp), default=str) + "\n")

        return str(self._output_dir / "manifest.json")

    @staticmethod
    def _sample(inst, resp: SUTResponse) -> dict:
        return {
            "instance_id": inst.instance_id,
            "question": inst.question,
            "answer": resp.answer,
            "retrieved_ep_ids": list(resp.retrieved_ep_ids),
            "retrieved_fact_ids": list(resp.retrieved_fact_ids),
            "retrieved_session_ids": list(resp.retrieved_session_ids),
            "depth": resp.depth_reached,
            "tokens_measured": resp.tokens_consumed_measured,
            "latency_ms": resp.latency_ms,
            "total_query_latency_ms": resp.total_query_latency_ms,
        }


__all__ = ["JsonReporter"]
