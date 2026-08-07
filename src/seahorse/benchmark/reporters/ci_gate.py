"""``CIGate`` — CI exit codes 0/10/3 (f5-16 §6.4, PRML v0.1 compatible).

- 0 = Pass — all metrics within expected bounds.
- 10 = Fail — a metric is below its threshold.
- 3 = Tampered — the manifest re-serialization does not match the file
  (integrity violation).
"""

from __future__ import annotations

import json
from pathlib import Path

from seahorse.benchmark.contracts import MetricResult
from seahorse.benchmark.reporters.manifest import RunManifest, _manifest_data


class CIGate:
    """Evaluates a run against thresholds and verifies manifest integrity."""

    EXIT_PASS = 0
    EXIT_FAIL = 10
    EXIT_TAMPERED = 3

    def __init__(self, thresholds: dict[str, float] | None = None) -> None:
        self._thresholds = thresholds or {}

    def evaluate(self, metric_results: list[MetricResult]) -> int:
        """0 = pass, 10 = fail (a metric below its threshold)."""
        for name, threshold in self._thresholds.items():
            report = next(
                (r.report for r in metric_results if r.metric_name == name), None
            )
            if report is not None and report.global_value < threshold:
                return self.EXIT_FAIL
        return self.EXIT_PASS

    def verify_tamper(self, manifest: RunManifest, path: str | Path) -> int:
        """3 = tampered (re-serialization mismatch), else 0."""
        canonical = _manifest_data(manifest)
        try:
            loaded = json.loads(Path(path).read_text("utf-8"))
        except Exception:  # noqa: BLE001 — unreadable/invalid JSON is tampered
            return self.EXIT_TAMPERED
        if loaded != canonical:
            return self.EXIT_TAMPERED
        return self.EXIT_PASS


__all__ = ["CIGate"]
