"""``CorpusBuilder`` — skip-mode ingestion of the benchmark haystack.

The corpus is ingested via ``MemoryFacade.remember`` with
``extraction_mode=skip`` (zero LLM, deterministic, reproducible bit-a-bit). The
``AdvancingClock`` is the deterministic AND temporally ordered clock:
``base + i*delta`` per call — a constant ``FixedClock`` would degenerate
``created_at desc`` ordering, ``age_days``, and the "newest version"
determination in knowledge-update.

The builder collects the union of all haystack sessions across instances and
delegates to the SUT's ``ingest`` (which owns the per-turn ``remember`` and the
retrieval bridge). Returns the ``fact_id → session_id`` map.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.benchmark.contracts import BenchmarkDataset
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT

_DEFAULT_BASE = datetime(2026, 1, 1, tzinfo=UTC)


class AdvancingClock:
    """Deterministic AND temporally ordered: ``base + i*delta`` per call.

    Reproducible across runs (same sequence) AND preserves temporal ordering.
    Controls the engine's ``created_at`` via the facade's injectable clock;
    hybrid retrieval owns its own UTC clock (a declared limitation).
    """

    def __init__(self, base: datetime, delta_seconds: float = 1.0) -> None:
        self._base = base
        self._delta = timedelta(seconds=delta_seconds)
        self._i = 0

    def __call__(self) -> datetime:
        t = self._base + self._i * self._delta
        self._i += 1
        return t

    def position(self) -> datetime:
        """The current time WITHOUT advancing (warm-DB variant).

        After a corpus ingest, ``position()`` is the next ``now`` a query would
        see — the warm-DB variant clock seeds from it so the recency boost reads
        the same ``now`` vs ``created_at`` spread as a fresh-DB run.
        """
        return self._base + self._i * self._delta


def earliest_session_date(dataset: BenchmarkDataset) -> datetime:
    """The clock base: the earliest session date across the haystack."""
    dates = [
        session["date"]
        for inst in dataset.instances
        for session in inst.haystack
        if session.get("date") is not None
    ]
    return min(dates) if dates else _DEFAULT_BASE


class CorpusBuilder:
    """Ingests the benchmark haystack into the SUT (skip-mode, deterministic).

    The first release is Seahorse-only: the builder reads the
    ``fact_id_to_session`` bridge the ``SeahorseSUT`` owns. External SUTs (a
    medium-term goal) would expose their own bridge.
    """

    def __init__(self, sut: SeahorseSUT) -> None:
        self._sut = sut

    def ingest(self, dataset: BenchmarkDataset) -> dict[str, str]:
        """Ingest all haystack sessions (deduplicated by session_id); return the
        fact_id→session_id bridge.

        Sessions repeat across questions (each question carries its own
        haystack); deduplicating by ``session_id`` avoids redundant ingestion.
        """
        seen: set[str] = set()
        sessions: list[dict] = []
        for inst in dataset.instances:
            for session in inst.haystack:
                sid = session["session_id"]
                if sid in seen:
                    continue
                seen.add(sid)
                sessions.append(session)
        self._sut.ingest(sessions)
        return self._sut.fact_id_to_session


__all__ = ["AdvancingClock", "CorpusBuilder", "earliest_session_date"]
