"""The primitives facade — the canonical Python API.

Re-exports the payload types, error vocabulary, the bi-temporal / disclosure
symbols the facade surface needs, and ``MemoryFacade`` itself. The MCP server
and the CLI import the canonical shapes from here — no transformation.
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
    # disclosure symbols re-exported (canonical for the MCP server and CLI)
    "PITPoint",
    "IndexRow",
    "TimelineWindow",
    "FullDetail",
    "TOP_K",
    "FullBatchTooLarge",
    "PitFullNotSupported",
    "NotInMVP0",
    # return types of the facade primitives (canonical for the benchmark
    # skeleton — the skeleton imports ONLY from seahorse.facade)
    "WriteResult",
    "Episode",
    # id generation re-exported so the CLI does not reach into the engine
    # directly — the facade is the only extension point.
    "new_uuid7",
]