"""``MarkdownReporter`` — human-readable report.md with comparison tables."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkDataset,
    MetricResult,
    SUTResponse,
)
from seahorse.benchmark.reporters.manifest import RunManifest


class MarkdownReporter:
    """Renders a run to a human-readable markdown report."""

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
        lines = [
            f"## Seahorse Benchmark Report — {dataset.name}-{dataset.config}",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for r in metric_results:
            lines.append(f"| {r.metric_name} | {r.report.global_value:.4f} |")
        lines.extend(
            [
                "",
                f"*run_id: {manifest.fingerprint.run_id}*",
                f"*score_source: {manifest.fingerprint.score_source}*",
                f"*reader: {manifest.fingerprint.reader_model_used}*",
                f"*judge: {manifest.fingerprint.judge_model_used}*",
                f"*judge validation: {manifest.fingerprint.judge_validation_status}*",
                f"*dataset: {dataset.name}-{dataset.config} v{dataset.version} "
                f"split={dataset.split_hash[:12]}*",
                f"*reproducibility: {manifest.fingerprint.reproducibility_class} "
                f"({manifest.fingerprint.expected_match_rate} expected match)*",
            ]
        )
        if manifest.run_errors:
            lines.append("")
            lines.append(f"*skipped instances (errors): {', '.join(manifest.run_errors)}*")
        path = self._output_dir / "report.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)


__all__ = ["MarkdownReporter"]
