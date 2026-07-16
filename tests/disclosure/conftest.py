"""Fakes for #8 DisclosureShaper unit tests.

Lightweight in-memory implementations of ``EpisodeIndexRepository`` and
``EpisodeRepository`` conforming to the contracts. They isolate #8's projection
logic from #6's SQL (already covered by 149 persistence tests) and keep the
disclosure tests fast and focused on shaper behavior: score passthrough, PIT
composition, deterministic truncation, caps, and fail-loud guards.

PIT predicates mirror #6's ``_pit_predicate`` exactly (ADR-03, never mix axes):
- state_at(t): valid_at IS NOT NULL AND valid_at <= t AND (invalid_at IS NULL OR invalid_at > t)
- known_at(t): created_at <= t AND (expired_at IS NULL OR expired_at > t)
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

import pytest

from seahorse.contracts.episode import Episode
from seahorse.contracts.index import IndexRowData, PITKind


class FakeIndexRepo:
    """In-memory ``EpisodeIndexRepository`` for #8 tests."""

    def __init__(self) -> None:
        self.rows: dict[str, IndexRowData] = {}

    def add(self, row: IndexRowData) -> None:
        self.rows[row.ep_id] = row

    def _row(self, ep_id: str) -> IndexRowData | None:
        return self.rows.get(ep_id)

    @staticmethod
    def _pit_ok(row: IndexRowData, pit_kind: PITKind, t: datetime) -> bool:
        if pit_kind == "state_at":
            return (
                row.valid_at is not None
                and row.valid_at <= t
                and (row.invalid_at is None or row.invalid_at > t)
            )
        # known_at
        return row.created_at <= t and (row.expired_at is None or row.expired_at > t)

    # INDEX level
    def get_rows(self, ep_ids: Sequence[str]) -> list[IndexRowData]:
        return [self.rows[i] for i in ep_ids if i in self.rows]

    def get_rows_state_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]:
        return [
            r
            for i in ep_ids
            if (r := self.rows.get(i)) is not None and self._pit_ok(r, "state_at", t)
        ]

    def get_rows_known_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]:
        return [
            r
            for i in ep_ids
            if (r := self.rows.get(i)) is not None and self._pit_ok(r, "known_at", t)
        ]

    # TIMELINE MVP-0
    def chain_rows_from(self, ep_id: str) -> list[IndexRowData]:
        # Transitive closure over supersedes (both directions), sorted by created_at.
        seen: set[str] = set()
        result: list[IndexRowData] = []
        # backward: follow supersedes pointers from ep_id
        current = ep_id
        while current is not None and current not in seen:
            seen.add(current)
            row = self.rows.get(current)
            if row is None:
                break
            result.append(row)
            current = row.supersedes
        # forward: rows whose supersedes points into the seen set
        frontier = {ep_id}
        while frontier:
            nxt: set[str] = set()
            for row in self.rows.values():
                if row.supersedes in frontier and row.ep_id not in seen:
                    seen.add(row.ep_id)
                    result.append(row)
                    nxt.add(row.ep_id)
            frontier = nxt
        result.sort(key=lambda r: r.created_at)
        return result

    def find_vigent_row_by_fact_id(
        self, fact_id: str, exclude: str | None = None
    ) -> IndexRowData | None:
        for row in self.rows.values():
            if row.fact_id == fact_id and row.invalid_at is None and row.expired_at is None:
                if exclude is not None and row.ep_id == exclude:
                    continue
                return row
        return None

    # TIMELINE MVP-1 axes — not exercised by #8 MVP-0 (raise if called).
    def range_rows_state_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]:
        raise NotImplementedError

    def range_rows_known_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]:
        raise NotImplementedError

    def bfs_neighbors_state_at(
        self, ep_id: str, pit: datetime, *, pit_kind: PITKind, hops: int, include_tags_soft: bool
    ) -> list[IndexRowData]:
        raise NotImplementedError


class FakeEpisodeRepo:
    """In-memory ``EpisodeRepository`` for #8 FULL-level tests."""

    def __init__(self) -> None:
        self.eps: dict[str, Episode] = {}

    def add(self, ep: Episode) -> None:
        self.eps[ep.id] = ep

    def append(self, episode: Episode) -> None:
        self.eps[episode.id] = episode

    def set_invalid_at(self, ep_id: str, now: datetime) -> None:
        ep = self.eps[ep_id]
        self.eps[ep_id] = Episode(
            **{**ep.__dict__, "invalid_at": now}  # type: ignore[arg-type]
        )

    def get(self, ep_id: str) -> Episode | None:
        return self.eps.get(ep_id)

    def find_vigent_by_fact_id(self, fact_id: str, exclude: str | None = None) -> Episode | None:
        for ep in self.eps.values():
            if ep.fact_id == fact_id and ep.invalid_at is None:
                if exclude is not None and ep.id == exclude:
                    continue
                return ep
        return None

    def chain_from(self, ep_id: str) -> list[Episode]:
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
        return result

    def query_vigent(self, subject: str | None = None) -> list[Episode]:
        return [
            ep
            for ep in self.eps.values()
            if ep.invalid_at is None and (subject is None or ep.subject == subject)
        ]

    def query_state_at(self, t: datetime, subject: str | None = None) -> list[Episode]:
        return [
            ep
            for ep in self.eps.values()
            if ep.valid_at is not None
            and ep.valid_at <= t
            and (ep.invalid_at is None or ep.invalid_at > t)
            and (subject is None or ep.subject == subject)
        ]

    def query_known_at(self, t: datetime, subject: str | None = None) -> list[Episode]:
        return [
            ep
            for ep in self.eps.values()
            if ep.created_at <= t
            and (subject is None or ep.subject == subject)
        ]

    @contextmanager
    def atomic(self) -> Iterator[None]:
        yield


# ---------------------------------------------------------------------------
# Pytest fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture()
def index() -> FakeIndexRepo:
    return FakeIndexRepo()


@pytest.fixture()
def repo() -> FakeEpisodeRepo:
    return FakeEpisodeRepo()