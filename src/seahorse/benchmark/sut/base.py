"""Shared SUT interface — re-exported from contracts (f5-16 §2.2).

``MemorySystemSUT`` is the interface BOTH Seahorse and external baselines
(Mem0/Zep/Letta, mediano) implement. The skeleton never calls internal
components — only this interface + the #12 facade.
"""

from __future__ import annotations

from seahorse.benchmark.contracts import MemorySystemSUT

__all__ = ["MemorySystemSUT"]
