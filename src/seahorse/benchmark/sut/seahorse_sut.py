"""``SeahorseSUT`` — adapter #12 ``MemoryFacade`` → the benchmark SUT interface.

Delegation purity (f5-16 §2.4): the SUT knows ONLY #12 (``MemoryFacade``) and
its return types (``WriteResult``, ``list[IndexRow]``, ``TimelineWindow``,
``list[FullDetail]``, ``Episode``). It never imports #2/#6/#11 internals.

The SUT owns the retrieval bridge (f5-16 §3.7): ``fact_id_to_session`` is
populated from each ``WriteResult.fact_id`` after ``remember``; the accurate
``ep_id_to_session`` map (which also covers the new versions created by
``improve``) resolves ``retrieved_session_ids`` for the metrics. ``fact_key_to_ep_id``
lets the ``KnowledgeUpdateSimulator`` resolve the old version of a fact.

``score_source`` is the experiment variant (f7 §3); the SUT detects the honest
regime at runtime — if every recall score is 0.0, the hybrid retrieval is not
wired and the manifest reports ``fallback_g2`` (ADR-10 honesty).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from seahorse.benchmark.contracts import SUTResponse
from seahorse.disclosure.types import PITPoint
from seahorse.facade import MemoryFacade
from seahorse.facade.errors import PitRecallNotSupportedMVP0
from seahorse.facade.types import Provenance, RememberPayload

_MIN_DT = datetime.min.replace(tzinfo=UTC)


class SeahorseSUT:
    """Adapts #12 ``MemoryFacade`` to the benchmark SUT interface."""

    def __init__(
        self,
        facade: MemoryFacade,
        facade_factory: Callable[[], MemoryFacade],
        *,
        reader_llm: Any,
        tokenizer: Any,
        fact_id_to_session: dict[str, str],
        agent_id: str = "seahorse-benchmark",
        top_k: int = 10,
        enable_progressive_disclosure: bool = False,
        temporal_mode: bool = False,
        score_source: str = "mvp1_rrf",
        recency_config: dict | None = None,
        rerank_enabled: bool = False,
        embed_mode: str = "body",
        ep_id_to_session: dict[str, str] | None = None,
        fact_key_to_ep_id: dict[str, str] | None = None,
    ) -> None:
        self._facade = facade
        self._facade_factory = facade_factory
        self._reader_llm = reader_llm
        self._tokenizer = tokenizer
        self._agent_id = agent_id
        self._top_k = top_k
        self._enable_pd = enable_progressive_disclosure
        self._temporal_mode = temporal_mode
        self._score_source = score_source
        self._recency_config = recency_config
        self._rerank_enabled = rerank_enabled
        self._embed_mode = embed_mode
        # Retrieval bridge (f5-16 §3.7): fact_id→session (spec contract) +
        # ep_id→session (accurate, covers improve-created versions) +
        # fact_key→ep_id (for the KnowledgeUpdateSimulator). The warm-DB path
        # (f7 §5a) pre-populates the bridge from a shared corpus template so a
        # variant SUT over a copied DB skips re-ingestion.
        self.fact_id_to_session = fact_id_to_session
        self._ep_id_to_session: dict[str, str] = dict(ep_id_to_session or {})
        self.fact_key_to_ep_id: dict[str, str] = dict(fact_key_to_ep_id or {})
        self._detected_score_source: str | None = None

    # ------------------------------------------------------------------ ingest

    def ingest(self, sessions: Sequence[dict]) -> list[str]:
        """Ingest haystack sessions via #12.remember with extraction_mode=skip.

        ``source_type='human'`` + ``valid_at=session_date`` in temporal mode
        (I2 permits arbitrary valid_at for human; the guard forces skip — f5-16
        §3.4). Sessions are ordered by date (oldest first) so the first version
        of a fact is ingested before its successor. Returns the ep_ids created.
        """
        ep_ids: list[str] = []
        ordered = sorted(
            sessions, key=lambda s: s.get("date") or _MIN_DT
        )
        for session in ordered:
            session_id = session["session_id"]
            date = session.get("date")
            for turn in session.get("turns", []):
                payload = RememberPayload(
                    body=turn["body"],
                    by=Provenance(
                        source_type="human" if self._temporal_mode else "agent",
                        agent_id=self._agent_id,
                        session_id=session_id,
                    ),
                    valid_at=date if self._temporal_mode else None,
                    title=turn.get("title"),
                    summary=turn.get("summary"),
                )
                wr = self._facade.remember(payload, skip_extraction=True)
                if wr.ep_id is not None:
                    ep_ids.append(wr.ep_id)
                    self._ep_id_to_session[wr.ep_id] = session_id
                    key = turn.get("fact_key")
                    if key is not None:
                        self.fact_key_to_ep_id.setdefault(key, wr.ep_id)
                if wr.fact_id is not None:
                    self.fact_id_to_session[wr.fact_id] = session_id
        return ep_ids

    # ------------------------------------------------- knowledge updates

    def apply_knowledge_updates(self, updates: Sequence[dict]) -> list[str]:
        """Create supersedes chains via #12.improve (f5-16 §4.6).

        Each update carries a resolved ``old_ep_id``; ``improve`` invalidates it
        and appends the new version. Returns the new ep_ids (tracked for
        ``knowledge_update_accuracy``).
        """
        new_ep_ids: list[str] = []
        for u in updates:
            old_ep_id = u["old_ep_id"]
            new_body = u["new_body"]
            by = Provenance(
                source_type="human" if self._temporal_mode else "agent",
                agent_id=self._agent_id,
                session_id=f"bench-update-{u['session_id']}",
            )
            new_ep = self._facade.improve(old_ep_id, new_body, by=by, reason="correction")
            new_ep_ids.append(new_ep.id)
            self._ep_id_to_session[new_ep.id] = u["session_id"]
            if new_ep.fact_id is not None:
                self.fact_id_to_session[new_ep.fact_id] = u["session_id"]
        return new_ep_ids

    # ------------------------------------------------------------------ query

    def query(self, question: str, *, question_date: datetime | None = None) -> SUTResponse:
        """Answer via #12.recall (INDEX) + the harness reader LLM.

        Token count is REAL via the reader tokenizer (f5-16 §4.4 F4), never
        ``len*50``. ``retrieved_session_ids`` is resolved via the ep_id bridge.
        """
        t0 = time.perf_counter()
        index_rows = self._recall(question, question_date)
        latency_index = (time.perf_counter() - t0) * 1000
        retrieved_ep_ids = tuple(r.ep_id for r in index_rows)
        retrieved_fact_ids = tuple(r.fact_id for r in index_rows)
        retrieved_session_ids = tuple(
            self._ep_id_to_session.get(ep, "") for ep in retrieved_ep_ids
        )
        context = self._format_index_rows(index_rows)
        tokens_measured = self._tokenizer.count(context)
        t0_reader = time.perf_counter()
        answer = self._reader_llm.generate(question, context, question_date)
        reader_latency = (time.perf_counter() - t0_reader) * 1000
        return SUTResponse(
            answer=answer,
            retrieved_ep_ids=retrieved_ep_ids,
            retrieved_fact_ids=retrieved_fact_ids,
            retrieved_session_ids=retrieved_session_ids,
            tokens_consumed_measured=tokens_measured,
            latency_ms={"index": latency_index},
            reader_latency_ms=reader_latency,
            total_query_latency_ms=latency_index + reader_latency,
            sut_metadata={
                "invalidated_ep_ids": [r.ep_id for r in index_rows if r.invalid_at is not None],
                "score_source": self._detect_score_source(index_rows),
            },
        )

    def _detect_score_source(self, rows) -> str:
        """Honest regime detection: all-zero scores ⇒ G2 fallback (ADR-10)."""
        if self._detected_score_source is None:
            if rows and all(r.score == 0.0 for r in rows):
                self._detected_score_source = "fallback_g2"
            else:
                self._detected_score_source = self._score_source
        return self._detected_score_source

    def _recall(self, question: str, question_date: datetime | None) -> Any:
        """#12.recall with the temporal PIT when the question anchors a date.

        f5-16 §3.4: temporal-reasoning questions evaluate with
        ``pit=state_at(question_date)`` in temporal mode, so the state as-of-the-
        question is what gets ranked (the old version, pre-update). Honest
        degrade (ADR-10): a regime without a PIT axis (G2, ADR-03) raises
        ``PitRecallNotSupportedMVP0`` from #12 → fall back to active-now, never
        crash the run.
        """
        if self._temporal_mode and question_date is not None:
            try:
                return self._facade.recall(
                    question, k=self._top_k, pit=PITPoint(kind="state_at", t=question_date)
                )
            except PitRecallNotSupportedMVP0:
                pass  # honest G2 degrade → active-now below
        return self._facade.recall(question, k=self._top_k)

    @staticmethod
    def _format_index_rows(rows) -> str:
        lines = []
        for i, r in enumerate(rows, 1):
            snippet = r.summary or r.subject or ""
            lines.append(f"{i}. [{r.subject}] {snippet}")
        return "\n".join(lines)

    # ------------------------------------------------------------ level probe

    def probe_level(self, question: str, level: str) -> dict:
        """Isolated level probe — latency/count WITHOUT the reader LLM.

        Called by the ``LevelProbeRunner`` for p95 TIMELINE/FULL in MVP-1 flat
        mode (f5-16 §3.6 F2).
        """
        if level == "index":
            t0 = time.perf_counter()
            rows = self._facade.recall(question, k=self._top_k)
            return {"latency_ms": (time.perf_counter() - t0) * 1000, "count": len(rows)}
        if level == "timeline":
            idx = self._facade.recall(question, k=1)
            if not idx:
                return {"latency_ms": 0.0, "count": 0}
            t0 = time.perf_counter()
            tw = self._facade.recall_timeline(idx[0].ep_id, axis="supersedes_chain")
            return {"latency_ms": (time.perf_counter() - t0) * 1000, "count": len(tw.entries)}
        if level == "full":
            idx = self._facade.recall(question, k=5)
            if not idx:
                return {"latency_ms": 0.0, "count": 0}
            t0 = time.perf_counter()
            fd = self._facade.recall_full([r.ep_id for r in idx[:5]])
            return {"latency_ms": (time.perf_counter() - t0) * 1000, "count": len(fd)}
        raise ValueError(f"unknown probe level: {level!r}")

    # ------------------------------------------------------------------ reset

    def reset(self) -> None:
        """Fresh facade via the factory + cleared bridges (fresh DB per run)."""
        self._facade = self._facade_factory()
        self.fact_id_to_session.clear()
        self._ep_id_to_session.clear()
        self.fact_key_to_ep_id.clear()
        self._detected_score_source = None

    # ---------------------------------------------------------------- identity

    def identity(self) -> dict:
        return {
            "sut_type": "seahorse",
            "reader_llm": self._reader_llm.identity(),
            "top_k": self._top_k,
            "progressive_disclosure": self._enable_pd,
            "temporal_mode": self._temporal_mode,
            "extraction_mode": "skip",
            "score_source": self._score_source,
            "recency_config": self._recency_config,
            "rerank_enabled": self._rerank_enabled,
            "embed_mode": self._embed_mode,
        }


__all__ = ["SeahorseSUT"]
