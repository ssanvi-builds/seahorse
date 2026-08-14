"""Benchmark dataset adapters — external benchmark → canonical form."""

from __future__ import annotations

from seahorse.benchmark.adapters.longmemeval import LMEBLoader  # noqa: F401  # registers 'lmeb'
from seahorse.benchmark.adapters.registry import AdapterRegistry

__all__ = ["AdapterRegistry", "LMEBLoader"]
