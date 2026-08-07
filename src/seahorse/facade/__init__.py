"""#12 Memory-Native Primitives Facade — the canonical Python API.

Re-exports the payload types, error vocabulary, the bi-temporal / disclosure
symbols the facade surface needs, and ``MemoryFacade`` itself. #13 (MCP) and
#14 (CLI) import the canonical shapes from here — no transformation.
"""

from __future__ import annotations

from seahorse.contracts.engine import WriteResult
from seahorse.contracts.episode import Episode
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
from seahorse.engine.ids import new_uuid7
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
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.factory import build_facade
from seahorse.facade.types import (
    COGNITIVE_TYPES,
    SOURCE_TYPES,
    FacadeConfig,
    Provenance,
    RecallPayload,
    RememberPayload,
)

__all__ = [
    # facade
    "MemoryFacade",
    "build_facade",
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
    # return types of #12 primitives (canonical for #16 benchmark skeleton —
    # the skeleton imports ONLY from seahorse.facade, delegation purity f5-16 §2.4)
    "WriteResult",
    "Episode",
    # id generation re-exported so #14 (CLI) does not reach into #2 (engine)
    # directly — the facade is the only seam (f5-14 §1, §2.3 uuid_extensions).
    "new_uuid7",
]