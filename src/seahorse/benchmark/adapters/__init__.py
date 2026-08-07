"""#16 benchmark adapters — external benchmark → canonical form (f5-16 §4)."""

from __future__ import annotations

from seahorse.benchmark.adapters.longmemeval import LMEBLoader  # noqa: F401  # registers 'lmeb'
from seahorse.benchmark.adapters.registry import AdapterRegistry

__all__ = ["AdapterRegistry", "LMEBLoader"]
