"""#16 benchmark metrics — retrieval / memory / efficiency + registry.

Shared statistical helpers (``_mean``, ``_p95``) live here so the metric
modules stay focused. The ``MetricRegistry`` is the pluggable registration
point (f5-16 §2.3): adding a metric = registering an instance, no runner
changes.
"""

from __future__ import annotations

import statistics


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _p95(values: list[float]) -> float:
    """Deterministic p95: sorted, index at ceil(0.95·n)-1 (no interpolation)."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = min(len(sorted_v) - 1, int(0.95 * len(sorted_v)))
    return sorted_v[idx]


__all__ = ["_mean", "_p95"]
