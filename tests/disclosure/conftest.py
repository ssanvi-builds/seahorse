"""Fakes for the progressive disclosure shaper unit tests.

Lightweight in-memory implementations of ``EpisodeIndexRepository`` and
``EpisodeRepository`` conforming to the contracts. They isolate the progressive
disclosure projection logic from the persistence layer's SQL (already covered by
149 persistence tests) and keep the disclosure tests fast and focused on shaper
behavior: score passthrough, PIT composition, deterministic truncation, caps, and
fail-loud guards.

PIT predicates mirror the persistence layer's ``_pit_predicate`` exactly (never
mix axes):
- state_at(t): (valid_at IS NULL OR valid_at <= t) AND (invalid_at IS NULL OR invalid_at > t)
  — valid_at IS NULL ("from forever") is valid at any t → INCLUDED.
- known_at(t): created_at <= t AND (expired_at IS NULL OR expired_at > t)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

import pytest

from seahorse.contracts.episode import Episode
from seahorse.contracts.index import IndexRowData, PITKind


class FakeIndexRepo:
    """In-memory ``EpisodeIndexRepository`` for progressive disclosure tests."""

    def __init__(self) -> None:
        self.rows: dict[str, IndexRowData] = {}

    def add(self, row: IndexRowData) -> None:
        self.rows[row.ep_id] = row

    def _row(self, ep_id: str) -> IndexRowData | None:
        return self.rows.get(ep_id)

    @staticmethod
    def _pit_ok(row: IndexRowData, pit_kind: PITKind, t: datetime) -> bool:
        if pit_kind == "state_at":
            # valid_at IS NULL = "from forever" — valid at ANY t. The predicate
            # is ``valid_at IS NULL OR valid_at <= t`` (mirrors the canonical
            # engine predicates get_vigente / is_valid_at, which already include
            # NULL). Real PENDING is valid_at in the FUTURE.
            return (
                (row.valid_at is None or row.valid_at <= t)
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

    # TIMELINE (first release)
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

    # TIMELINE range axes — mirror of the SQLite _range_rows (inclusive window,
    # NULL-excluded, optional subject filter, ordered by the axis column).
    def range_rows_state_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]:
        rows = [
            r
            for r in self.rows.values()
            if r.valid_at is not None and t_start <= r.valid_at <= t_end
        ]
        if subject is not None:
            rows = [r for r in rows if r.subject == subject]
        return sorted(rows, key=lambda r: r.valid_at)

    def range_rows_known_at(
        self, t_start: datetime, t_end: datetime, *, subject: str | None = None
    ) -> list[IndexRowData]:
        rows = [
            r
            for r in self.rows.values()
            if r.created_at is not None and t_start <= r.created_at <= t_end
        ]
        if subject is not None:
            rows = [r for r in rows if r.subject == subject]
        return sorted(rows, key=lambda r: r.created_at)

    def bfs_neighbors_state_at(
        self, ep_id: str, pit: datetime, *, pit_kind: PITKind, hops: int, include_tags_soft: bool
    ) -> list[IndexRowData]:
        """Supersedes-based BFS mirror of the SQLite impl.

        Traverses ``supersedes`` edges in both directions (rows pointing INTO
        the current layer, and rows the current layer points to), collecting
        rows that satisfy the PIT predicate at each layer.
        """
        if include_tags_soft:
            raise NotImplementedError
        seen: set[str] = {ep_id}
        current_layer: set[str] = {ep_id}
        collected: list[IndexRowData] = []
        for _depth in range(hops + 1):
            if not current_layer:
                break
            for ep in current_layer:
                row = self.rows.get(ep)
                if row is not None and self._pit_ok(row, pit_kind, pit):
                    collected.append(row)
            newer = {r.ep_id for r in self.rows.values() if r.supersedes in current_layer}
            older = {
                r.supersedes
                for r in self.rows.values()
                if r.ep_id in current_layer and r.supersedes is not None
            }
            next_layer = (newer | older) - seen
            seen |= next_layer
            current_layer = next_layer
        result = sorted(collected, key=lambda r: r.ep_id)
        return result


class FakeEpisodeRepo:
    """In-memory ``EpisodeRepository`` for progressive disclosure FULL-level tests."""

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


# ---------------------------------------------------------------------------
# Recording doubles — structurally enforce the disclosure layer's delegation /
# guard-order invariants that outcome-only tests cannot catch (adversarial
# review items).
# ---------------------------------------------------------------------------


class RecordingIndex(FakeIndexRepo):
    """``FakeIndexRepo`` that counts per-accessor calls.

    Used to prove the disclosure layer DELEGATES PIT to the persistence
    layer's typed accessors (``get_rows_state_at`` / ``get_rows_known_at``)
    instead of inlining the predicate (drift-prevention), and that guards fire
    BEFORE any index fetch.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = defaultdict(int)

    def get_rows(self, ep_ids: Sequence[str]) -> list[IndexRowData]:
        self.calls["get_rows"] += 1
        return super().get_rows(ep_ids)

    def get_rows_state_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]:
        self.calls["get_rows_state_at"] += 1
        return super().get_rows_state_at(ep_ids, t)

    def get_rows_known_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]:
        self.calls["get_rows_known_at"] += 1
        return super().get_rows_known_at(ep_ids, t)

    def chain_rows_from(self, ep_id: str) -> list[IndexRowData]:
        self.calls["chain_rows_from"] += 1
        return super().chain_rows_from(ep_id)

    def find_vigent_row_by_fact_id(
        self, fact_id: str, exclude: str | None = None
    ) -> IndexRowData | None:
        self.calls["find_vigent_row_by_fact_id"] += 1
        return super().find_vigent_row_by_fact_id(fact_id, exclude)


class CountingEpisodeRepo(FakeEpisodeRepo):
    """``FakeEpisodeRepo`` that counts ``get`` calls.

    Used to prove the FULL-level guards (``FullBatchTooLarge``,
    ``PitFullNotSupported``) fire BEFORE any episode fetch.
    """

    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    def get(self, ep_id: str) -> Episode | None:
        self.get_calls += 1
        return super().get(ep_id)


@pytest.fixture()
def rec_index() -> RecordingIndex:
    return RecordingIndex()


@pytest.fixture()
def counting_repo() -> CountingEpisodeRepo:
    return CountingEpisodeRepo()