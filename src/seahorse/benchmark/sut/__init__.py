"""SUT adapters — the shared ``MemorySystemSUT`` interface + SeahorseSUT."""

from __future__ import annotations

from seahorse.benchmark.contracts import MemorySystemSUT
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT

__all__ = ["MemorySystemSUT", "SeahorseSUT"]
