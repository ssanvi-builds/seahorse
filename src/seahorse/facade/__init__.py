"""#12 Memory-Native Primitives Facade — the canonical Python API.

Re-exports the payload types, error vocabulary, and the bi-temporal / disclosure
symbols the facade surface needs. ``MemoryFacade`` itself lands in a later
commit of this component (``facade.py``). #13 (MCP) and #14 (CLI) import the
canonical shapes from here — no transformation.
"""

from __future__ import annotations

from seahorse.disclosure.types import (
    TOP_K,
    FullBatchTooLarge,
    FullDetail,
    IndexRow,
    NotInMVP0,
    PitFullNotSupported,
    PITPoint,
    TimelineWindow,
)
from seahorse.facade.errors import (
    E_EMPTY_BODY,
    E_EMPTY_QUERY,
    E_INVALID_EXTRACTION_MODE,
    E_INVALID_PIT_KIND,
    E_MISSING_SOURCE_TYPE,
    E_NOT_IN_MVP_0_1,
    E_PIT_RECALL_MVP_0,
    E_PIT_REQUIRES_T,
    EmptyQueryError,
    InvalidPITKind,
    PitRecallNotSupportedMVP0,
    SeahorseError,
)
from seahorse.facade.types import (
    COGNITIVE_TYPES,
    SOURCE_TYPES,
    FacadeConfig,
    Provenance,
    RecallPayload,
    RememberPayload,
)

__all__ = [
    # payloads + config
    "Provenance",
    "RememberPayload",
    "RecallPayload",
    "FacadeConfig",
    "COGNITIVE_TYPES",
    "SOURCE_TYPES",
    # errors
    "SeahorseError",
    "InvalidPITKind",
    "PitRecallNotSupportedMVP0",
    "EmptyQueryError",
    "E_EMPTY_BODY",
    "E_MISSING_SOURCE_TYPE",
    "E_INVALID_EXTRACTION_MODE",
    "E_EMPTY_QUERY",
    "E_INVALID_PIT_KIND",
    "E_PIT_REQUIRES_T",
    "E_NOT_IN_MVP_0_1",
    "E_PIT_RECALL_MVP_0",
    # disclosure symbols re-exported (canonical for #13/#14)
    "PITPoint",
    "IndexRow",
    "TimelineWindow",
    "FullDetail",
    "TOP_K",
    "FullBatchTooLarge",
    "PitFullNotSupported",
    "NotInMVP0",
]