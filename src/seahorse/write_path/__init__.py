"""#5 Write Path — the single write entry behind #12/#13/#14 (ADR-09, SO-5b).

``WritePath`` is the Protocol ``MemoryFacade.remember`` delegates to. MVP-0
ships ``StubWritePath`` (skip path real, llm→skip degrade). The first-class
decision tree (formal ``is_valid_skip_path`` gate + ``deterministic_extract``
fallback + real llm path) is MVP-1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import ExtractionMode, RememberPayload
from seahorse.write_path.decide import (
    InvalidExtractionMode,
    Path,
    PathDecision,
    decide_path,
)
from seahorse.write_path.extract import (
    ExtractedCandidate,
    SubjectDerivationError,
    deterministic_extract,
)
from seahorse.write_path.stub import StubWritePath, _degrade_to_skip, run_skip_path


@runtime_checkable
class WritePath(Protocol):
    """The single write-path seam ``MemoryFacade.remember`` delegates to.

    ``ingest`` picks the path deterministically (``decide_path``, no LLM) and
    executes it: the skip path for real, or the llm path (degraded to skip in
    MVP-0). Returns the engine ``WriteResult`` verbatim.
    """

    def ingest(
        self,
        payload: RememberPayload,
        extraction_mode: ExtractionMode,
        *,
        now: datetime | None = ...,
    ) -> WriteResult: ...


__all__ = [
    "WritePath",
    "StubWritePath",
    "PathDecision",
    "Path",
    "InvalidExtractionMode",
    "ExtractedCandidate",
    "SubjectDerivationError",
    "decide_path",
    "deterministic_extract",
    "run_skip_path",
    "_degrade_to_skip",
]