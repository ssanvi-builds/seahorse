"""The facade collaborator Protocols (C4 prep) — their own module.

The five structural collaborator surfaces (_ShaperLike, _WritePathLike,
_MaterializerLike, _EngineLike, _RetrieverLike) single-home in
``facade/protocols.py``; ``facade.py`` re-exports them so annotations and the
white-box tests keep resolving.
"""

from __future__ import annotations

from seahorse.facade import facade as facade_mod
from seahorse.facade.facade import (
    _RetrieverLike,
)
from seahorse.facade.protocols import (
    _EngineLike as EngineLikeP,
)
from seahorse.facade.protocols import (
    _MaterializerLike as MaterializerLikeP,
)
from seahorse.facade.protocols import (
    _RetrieverLike as RetrieverLikeP,
)
from seahorse.facade.protocols import (
    _ShaperLike as ShaperLikeP,
)
from seahorse.facade.protocols import (
    _WritePathLike as WritePathLikeP,
)


def test_facade_reexports_are_identities():
    assert facade_mod._ShaperLike is ShaperLikeP
    assert facade_mod._WritePathLike is WritePathLikeP
    assert facade_mod._MaterializerLike is MaterializerLikeP
    assert facade_mod._EngineLike is EngineLikeP
    assert facade_mod._RetrieverLike is RetrieverLikeP


def test_protocol_surfaces():
    for member in ("materialize_index", "materialize_timeline", "materialize_full"):
        assert callable(getattr(ShaperLikeP, member))
    for member in ("ingest",):
        assert callable(getattr(WritePathLikeP, member))
    for member in ("materialize", "invalidate"):
        assert callable(getattr(MaterializerLikeP, member))
    for member in (
        "get",
        "get_vigente",
        "improve",
        "forget",
        "freshness_view",
        "audit_log",
        "follow_supersedes_chain",
    ):
        assert callable(getattr(EngineLikeP, member))
    for member in ("recall",):
        assert callable(getattr(RetrieverLikeP, member))


class _FakeRetriever:
    supports_pit = True

    def recall(
        self,
        query: str,
        *,
        pit=None,
        k=10,
        cognitive_type=None,
        subject_filter=None,
        session_boost=False,
    ) -> tuple:
        return ()


def test_runtime_checkable_satisfies_retriever_protocol():
    assert isinstance(_FakeRetriever(), _RetrieverLike)