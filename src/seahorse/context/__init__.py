"""Context injection layer (obsiforge §6).

The bootstrap is by RECENCY, not semantics (claude-mem does not inject semantic
context — it injects recency + summaries + a fetch pointer; Seahorse replicates
this, §6.1). ``MemoryFacade.context()`` assembles the four INDEX-level blocks;
this module renders them to the bootstrap text the SessionStart hook injects.
"""

from seahorse.context.assembler import render_context

__all__ = ["render_context"]
