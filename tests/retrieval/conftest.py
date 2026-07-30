"""Fakes for #11 Hybrid Retrieval tests.

Lightweight in-memory doubles of the injected Protocols (#6 vector/FTS/index
repos, #2 episode repo, #7 query embedder). They RECORD call arguments so the
tests can assert axis-isolation (R13: the SAME ``pit.kind`` fans to ALL sources;
no source ever sees the other axis) and routing (vigent knn gets
``cognitive_types`` pushdown; PIT knn + ALL BM25 do NOT — G1 client-side).

PIT predicates for the chain projection are MIRRORED from
``tests/disclosure/conftest._pit_ok`` (ADR-03: axes never mixed). The fake repos
themselves do NOT apply PIT — that is #6's job in production; here the fake just
returns whatever the test configured, so the tests isolate #11's routing from
#6's predicate. The chain projection (``_project_chain``) applies PIT itself
(read-only projection of the supersedes chain), so ``FakeEpisodeRepo.chain_from``
returns the WHOLE chain and #11 filters it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from seahorse.contracts.episode import Episode
from seahorse.contracts.index import IndexRowData, PITKind
from seahorse.contracts.persistence import FullTextHit, VectorHit

# ---------------------------------------------------------------------------
# Episode / row builders — minimal aware-UTC instances for the fakes.
# ---------------------------------------------------------------------------


def _ep(
    ep_id: str,
    *,
    created_at: datetime,
    cognitive_type: str = "fact",
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
    schema_version: str = "F3.1",
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=created_at,
        schema_version=schema_version,
        provenance={"source_type": "manual"},
        cognitive_type=cognitive_type,
        valid_at=valid_at,
        invalid_at=invalid_at,
        expired_at=expired_at,
        supersedes=supersedes,
    )


def _row(
    ep_id: str,
    *,
    created_at: datetime,
    cognitive_type: str = "fact",
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
) -> IndexRowData:
    return IndexRowData(
        ep_id=ep_id,
        fact_id=f"fact-{ep_id}",
        subject=f"subject-{ep_id}",
        title=None,
        summary=None,
        cognitive_type=cognitive_type,
        source_type=None,
        schema_version="F3.1",
        skip_extraction=False,
        valid_at=valid_at,
        invalid_at=invalid_at,
        created_at=created_at,
        expired_at=expired_at,
        supersedes=supersedes,
    )


# ---------------------------------------------------------------------------
# Recording fakes.
# ---------------------------------------------------------------------------


class FakeQueryEmbedder:
    """``QueryEmbedder`` double. Records the query; returns a sentinel vector.

    C8.4 widened the Protocol (``embedding_dim`` + ``embed_queries``); the fake
    carries both so it stays structurally conformant. #11's hot path only calls
    ``embed_query``; ``embed_queries`` records for parity/forward-compat tests.
    """

    embedding_dim: int = 0  # sentinel — #11 never inspects the opaque vector

    def __init__(self, vec: Any = "VEC") -> None:
        self.calls: list[str] = []
        self.vec = vec

    def embed_query(self, query: str) -> Any:
        self.calls.append(query)
        return self.vec

    def embed_queries(self, texts: Sequence[str]) -> Any:
        self.calls.extend(texts)
        return [self.vec for _ in texts]


class FakeVectorRepo:
    """``VectorIndexRepository`` double. Records per-method call args."""

    def __init__(self) -> None:
        self.calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.knn_hits: list[VectorHit] = []
        self.knn_state_at_hits: list[VectorHit] = []
        self.knn_known_at_hits: list[VectorHit] = []

    def knn(
        self,
        query: Any,
        k: int,
        *,
        vigent_only: bool = True,
        fact_id_filter: str | None = None,
        cognitive_types: list[str] | None = None,
    ) -> list[VectorHit]:
        self.calls["knn"].append(
            {
                "query": query,
                "k": k,
                "vigent_only": vigent_only,
                "fact_id_filter": fact_id_filter,
                "cognitive_types": cognitive_types,
            }
        )
        return self.knn_hits

    def knn_state_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        self.calls["knn_state_at"].append({"query": query, "k": k, "t": t})
        return self.knn_state_at_hits

    def knn_known_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        self.calls["knn_known_at"].append({"query": query, "k": k, "t": t})
        return self.knn_known_at_hits

    def upsert(self, *a: Any, **kw: Any) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def distinct_model_identities(self) -> list[str]:  # pragma: no cover - unused
        raise NotImplementedError

    def remove_for_rebuild(self) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def rebuild(self) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def count(self) -> int:  # pragma: no cover - unused
        raise NotImplementedError


class FakeFtsRepo:
    """``FullTextIndexRepository`` double. Records per-method call args."""

    def __init__(self) -> None:
        self.calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.search_hits: list[FullTextHit] = []
        self.search_state_at_hits: list[FullTextHit] = []
        self.search_known_at_hits: list[FullTextHit] = []

    def search(
        self, query: str, k: int, *, vigent_only: bool = True, subject_filter: str | None = None
    ) -> list[FullTextHit]:
        self.calls["search"].append(
            {"query": query, "k": k, "vigent_only": vigent_only, "subject_filter": subject_filter}
        )
        return self.search_hits

    def search_state_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]:
        self.calls["search_state_at"].append({"query": query, "k": k, "t": t})
        return self.search_state_at_hits

    def search_known_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]:
        self.calls["search_known_at"].append({"query": query, "k": k, "t": t})
        return self.search_known_at_hits

    def upsert(self, doc: Any) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def remove_for_rebuild(self, ep_id: str) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def rebuild(self, docs: Any) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def count(self) -> int:  # pragma: no cover - unused
        raise NotImplementedError


class FakeEpisodeRepo:
    """``EpisodeRepository`` double. Stores episodes; ``chain_from`` follows
    ``supersedes`` (backward only — sufficient for projection tests)."""

    def __init__(self) -> None:
        self.eps: dict[str, Episode] = {}
        self.get_calls: list[str] = []
        self.chain_calls: list[str] = []

    def add(self, ep: Episode) -> None:
        self.eps[ep.id] = ep

    def get(self, ep_id: str) -> Episode | None:
        self.get_calls.append(ep_id)
        return self.eps.get(ep_id)

    def chain_from(self, ep_id: str) -> list[Episode]:
        self.chain_calls.append(ep_id)
        seen: set[str] = set()
        result: list[Episode] = []
        current: str | None = ep_id
        while current is not None and current not in seen:
            seen.add(current)
            ep = self.eps.get(current)
            if ep is None:
                break
            result.append(ep)
            current = ep.supersedes
        # Match the real SqliteEpisodeRepository.chain_from contract: sorted by
        # created_at asc (oldest-first), so the LAST element is the newest/current
        # version — which is what #11's _chain_active_now / _chain_vigent_at pick.
        result.sort(key=lambda e: e.created_at)
        return result

    def append(self, episode: Episode) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def set_invalid_at(self, ep_id: str, now: datetime) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def find_vigent_by_fact_id(
        self, fact_id: str, exclude: str | None = None
    ) -> Episode | None:  # pragma: no cover - unused
        raise NotImplementedError

    def query_vigent(
        self, subject: str | None = None
    ) -> list[Episode]:  # pragma: no cover - unused
        raise NotImplementedError

    def query_state_at(
        self, t: datetime, subject: str | None = None
    ) -> list[Episode]:  # pragma: no cover - unused
        raise NotImplementedError

    def query_known_at(
        self, t: datetime, subject: str | None = None
    ) -> list[Episode]:  # pragma: no cover - unused
        raise NotImplementedError

    @contextmanager
    def atomic(self) -> Iterator[None]:  # pragma: no cover - unused
        yield


class FakeBfsIndexRepo:
    """``EpisodeIndexRepository`` double for the BFS axis. Records the
    ``pit_kind``/``hops``/``t`` the engine passed (axis-isolation R13). Only
    ``bfs_neighbors_state_at`` is exercised; the rest raise."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.rows: list[IndexRowData] = []

    def bfs_neighbors_state_at(
        self,
        ep_id: str,
        pit: datetime,
        *,
        pit_kind: PITKind,
        hops: int,
        include_tags_soft: bool,
    ) -> list[IndexRowData]:
        self.calls.append(
            {
                "ep_id": ep_id,
                "pit": pit,
                "pit_kind": pit_kind,
                "hops": hops,
                "include_tags_soft": include_tags_soft,
            }
        )
        return self.rows

    # The remaining 8 accessors are unused by #11; raise to fail loud if reached.
    def get_rows(self, ep_ids: Sequence[str]) -> list[IndexRowData]:  # pragma: no cover
        raise NotImplementedError

    def get_rows_state_at(
        self, ep_ids: Sequence[str], t: datetime
    ) -> list[IndexRowData]:  # pragma: no cover
        raise NotImplementedError

    def get_rows_known_at(
        self, ep_ids: Sequence[str], t: datetime
    ) -> list[IndexRowData]:  # pragma: no cover
        raise NotImplementedError

    def chain_rows_from(self, ep_id: str) -> list[IndexRowData]:  # pragma: no cover
        raise NotImplementedError

    def find_vigent_row_by_fact_id(
        self, fact_id: str, exclude: str | None = None
    ) -> IndexRowData | None:  # pragma: no cover
        raise NotImplementedError

    def range_rows_state_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]:  # pragma: no cover
        raise NotImplementedError

    def range_rows_known_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Pytest fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedder() -> FakeQueryEmbedder:
    return FakeQueryEmbedder()


@pytest.fixture()
def vector_repo() -> FakeVectorRepo:
    return FakeVectorRepo()


@pytest.fixture()
def fts_repo() -> FakeFtsRepo:
    return FakeFtsRepo()


@pytest.fixture()
def episode_repo() -> FakeEpisodeRepo:
    return FakeEpisodeRepo()


@pytest.fixture()
def bfs_repo() -> FakeBfsIndexRepo:
    return FakeBfsIndexRepo()


@pytest.fixture()
def clock_now() -> datetime:
    """Fixed clock value for reproducible ``pit=None`` resolution."""
    return datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def fixed_clock(clock_now: datetime):
    """Injectable clock returning ``clock_now`` for ADR-10 reproducibility."""
    return lambda: clock_now


__all__ = [
    "FakeBfsIndexRepo",
    "FakeEpisodeRepo",
    "FakeFtsRepo",
    "FakeQueryEmbedder",
    "FakeVectorRepo",
    "_ep",
    "_row",
]
