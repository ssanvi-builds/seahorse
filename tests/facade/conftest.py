"""Shared fixtures + recording doubles for #12 MemoryFacade tests.

The recording doubles (``RecordingEngine`` / ``RecordingWritePath`` /
``RecordingShaper``) are added incrementally as the facade methods land. They
structurally enforce #12's delegation invariants that outcome-only tests cannot
catch (the lesson from #8's adversarial review): assert WHAT downstream method
was called, with WHICH args, in WHICH order — not just the return value.
"""

from __future__ import annotations