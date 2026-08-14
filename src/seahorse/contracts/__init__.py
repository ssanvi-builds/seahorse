"""Seahorse shared contracts — the stable typed frontier.

Symbols that cross component boundaries (Protocols the persistence layer
implements against, frozen dataclasses owned by other components) live here.
Components ship in dependency order; later components IMPORT from here, they
never relocate.

Ownership:
- Episode                -> the schema module (contracts.episode)
- EpisodeRepository      -> the engine (contracts.engine)
- AuditEvent             -> the engine (contracts.engine)
- freshness_of           -> the engine's pure derivation (contracts.engine);
                           shared with progressive disclosure
- IndexRowData, PITKind  -> progressive disclosure / the BFS axis (contracts.index)
- FusedCandidate         -> hybrid retrieval, materialized by progressive
                           disclosure (contracts.retrieval)
- QueryEmbedder          -> hybrid retrieval, materialized by the embedder (contracts.embeddings)
- 9 repository Protocols -> the persistence layer (contracts.persistence)
"""

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.contracts.engine import (
    AuditEvent,
    EpisodeRepository,
    FreshnessView,
    InvalidationConflictError,
    NotFound,
    WriteResult,
    freshness_of,
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
    ParsedNote,
    RebuildConflict,
    RebuildReport,
    ReindexJob,
    ReindexJobRepository,
    SidecarIndexRepository,
    VectorHit,
    VectorIndexRepository,
)
from seahorse.contracts.retrieval import FusedCandidate

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
    "FusedCandidate",
    "HopsCapExceeded",
    "InvalidationConflictError",
    "MAX_HOPS_MVP1",
    "IndexRowData",
    "NotFound",
    "PITKind",
    "ParsedNote",
    "QueryEmbedder",
    "RebuildConflict",
    "RebuildReport",
    "ReindexJob",
    "ReindexJobRepository",
    "SidecarIndexRepository",
    "VectorHit",
    "VectorIndexRepository",
    "WriteResult",
    "freshness_of",
]
