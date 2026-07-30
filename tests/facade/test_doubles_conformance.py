"""C8.5 — recording doubles must structurally satisfy the facade Protocols.

The facade's delegation invariants are pinned by recording doubles that assert
WHICH downstream method was called (``tests/facade/conftest.py``). Those
doubles only carry weight if they actually conform to the ``@runtime_checkable``
Protocols the real ``MemoryFacade`` types its collaborators against — otherwise a
double could drift from the Protocol (a missing method) and the facade would
accept it at construction (duck-typed) while the real impl would not, or vice
versa. C8.5 pins the conformance with ``isinstance`` so a double that drops a
method the Protocol requires fails loud here, not silently in an outcome test.

The Protocols are private to ``seahorse.facade.facade`` (``_EngineLike`` /
``_WritePathLike`` / ``_ShaperLike`` / ``_RetrieverLike``); importing them
white-box is the point — these are the exact shapes ``MemoryFacade.__init__``
type-checks against.
"""

from __future__ import annotations

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.facade.facade import (
    _EngineLike,
    _RetrieverLike,
    _ShaperLike,
    _WritePathLike,
)
from seahorse.facade.stub_embedder import StubQueryEmbedder
from seahorse.facade.types import FacadeConfig
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from tests.facade.conftest import RecordingEngine, RecordingShaper, RecordingWritePath


def _fixed_clock():
    from datetime import UTC, datetime

    return lambda: datetime(2026, 7, 16, tzinfo=UTC)


def test_recording_engine_satisfies_engine_protocol() -> None:
    assert isinstance(RecordingEngine(), _EngineLike)


def test_recording_write_path_satisfies_write_path_protocol() -> None:
    assert isinstance(RecordingWritePath(), _WritePathLike)


def test_recording_shaper_satisfies_shaper_protocol() -> None:
    assert isinstance(RecordingShaper(), _ShaperLike)


def test_vigente_retriever_satisfies_retriever_protocol() -> None:
    # The MVP-0 retriever (default at build_facade) must satisfy the recall-policy
    # seam — pins that swapping the real impl in MVP-1 keeps the facade delegate
    # valid without a facade-side signature change.
    retriever = VigenteListingRetriever(
        engine=RecordingEngine(), clock=_fixed_clock(), config=FacadeConfig()
    )
    assert isinstance(retriever, _RetrieverLike)


def test_stub_query_embedder_satisfies_embedder_protocol() -> None:
    # Mirrors tests/contracts/test_embeddings.py; kept here alongside the other
    # composition-root doubles so the conformance set is in one place.
    assert isinstance(StubQueryEmbedder(), QueryEmbedder)