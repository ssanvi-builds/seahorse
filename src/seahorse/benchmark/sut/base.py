"""Shared SUT interface — re-exported from contracts.

``MemorySystemSUT`` is the interface BOTH Seahorse and external baselines
(Mem0/Zep/Letta, a medium-term goal) implement. The harness never calls
internal components — only this interface + the ``MemoryFacade``.
"""

from __future__ import annotations

from seahorse.benchmark.contracts import MemorySystemSUT

__all__ = ["MemorySystemSUT"]
