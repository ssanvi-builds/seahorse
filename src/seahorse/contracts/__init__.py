"""Seahorse shared contracts — the stable typed frontier.

Symbols that cross component boundaries (Protocols #6 implements against, frozen
dataclasses owned by other components) live here. Components ship in order
#6 -> #2 -> ... -> #16; later components IMPORT from here, they never relocate.

Ownership:
- Episode                -> #1 (contracts.episode)
- EpisodeRepository      -> #2 (contracts.engine)
- AuditEvent             -> #2 (contracts.engine)
- IndexRowData, PITKind  -> #8 / #10 (contracts.index)
- 9 repository Protocols -> #6 (contracts.persistence)
"""

from seahorse.contracts.engine import (
    AuditEvent,
    EpisodeRepository,
    FreshnessView,
    InvalidationConflictError,
    NotFound,
    WriteResult,
)
from seahorse.contracts.episode import Episode
from seahorse.contracts.index import (
    MAX_HOPS_MVP1,
    HopsCapExceeded,
    IndexRowData,
    PITKind,
)
from seahorse.contracts.persistence import (
    AuditEventRepository,
    EmbeddingsCacheRepository,
    EpisodeIndexRepository,
    FtsDoc,
    FullTextHit,
    FullTextIndexRepository,
    ReindexJob,
    ReindexJobRepository,
    SidecarIndexRepository,
    VectorHit,
    VectorIndexRepository,
)

__all__ = [
    "AuditEvent",
    "AuditEventRepository",
    "EmbeddingsCacheRepository",
    "Episode",
    "EpisodeIndexRepository",
    "EpisodeRepository",
    "FreshnessView",
    "FtsDoc",
    "FullTextHit",
    "FullTextIndexRepository",
    "HopsCapExceeded",
    "InvalidationConflictError",
    "MAX_HOPS_MVP1",
    "IndexRowData",
    "NotFound",
    "PITKind",
    "ReindexJob",
    "ReindexJobRepository",
    "SidecarIndexRepository",
    "VectorHit",
    "VectorIndexRepository",
    "WriteResult",
]
