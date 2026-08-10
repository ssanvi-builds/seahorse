"""Observer worker — drains the queue, batches by session, writes episodes.

The worker is a client of #12 (``MemoryFacade``) — the engine never sees a
hook, only ``RememberPayload`` (delegation purity, obsiforge §4.2). It drains
the observer queue, groups events by SESSION (F7 decision (d) — por_sesion:
the turn is NOT a recoverable unit, f7-experiment-batch), renders each turn via
the deterministic batcher, and writes one episode per turn via
``facade.remember`` (skip-first, ADR-09).

Thresholding (§4.3): a turn without a user prompt → no episode; body < 40 chars
→ no episode; ``skip_tools`` discard the event; ``drop_tools`` discard the
event entirely. The OQ3 summary skips the H1 (§15.2 redesign 4).

Ack semantics: events are acked after a turn is consumed (written, collided, or
skipped). A write FAILURE leaves the events unacked → retried on the next drain
(the queue dedup makes the retry a no-op for already-written events).

References:
- obsiforge-evolution-architecture.md §4.3 (batcher, thresholding)
- obsiforge-evolution-architecture.md §4.6 (skip-first, summary OQ3)
- f7-experiment-batch.md (por_sesion decision)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from seahorse.disclosure.types import SUMMARY_MAX_CHARS
from seahorse.facade.types import RememberPayload
from seahorse.observe.batcher import (
    BODY_MAX_CHARS,
    build_title,
    render_turn_body,
)
from seahorse.observe.protocol import (
    EVENT_POST_TOOL_USE,
    EVENT_USER_PROMPT_SUBMIT,
    Envelope,
)
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.threshold import (
    DEFAULT_DROP_TOOLS,
    DEFAULT_SKIP_TOOLS,
    should_drop_event,
    should_skip_event,
)
from seahorse.write_path.extract import derive_summary

# §4.3: body < 40 chars → no episode.
MIN_BODY_CHARS = 40
# Short, stable session tag for the H1 (first N chars of the session_id).
SESSION_TAG_CHARS = 8

_logger = logging.getLogger("seahorse.observe.worker")


@dataclass(frozen=True)
class ObserverConfig:
    """Observer policy (the ``[observe]`` section of ``seahorse.toml``)."""

    skip_tools: frozenset[str] = DEFAULT_SKIP_TOOLS
    drop_tools: frozenset[str] = DEFAULT_DROP_TOOLS
    extraction_mode: str = "skip"  # skip | llm (skip-first default, ADR-09)
    body_max_chars: int = BODY_MAX_CHARS
    min_body_chars: int = MIN_BODY_CHARS
    summary_max_chars: int = SUMMARY_MAX_CHARS


@dataclass
class WorkerReport:
    """Drain outcome (mutable accumulator, local to one drain)."""

    events_read: int = 0
    episodes_written: int = 0
    turns_skipped: int = 0
    events_skipped: int = 0
    events_dropped: int = 0
    collisions: int = 0
    failures: int = 0


def short_session_tag(session_id: str) -> str:
    """Short, stable session tag for the H1 (first ``SESSION_TAG_CHARS`` chars).

    Stable across reprocess (same session_id → same tag); short enough to leave
    room for the prompt's first line in the title budget.
    """
    return session_id[:SESSION_TAG_CHARS]


class ObserverWorker:
    """Drains the observer queue and writes episodes via the facade."""

    def __init__(
        self,
        facade: Any,
        queue: ObserverQueue,
        *,
        config: ObserverConfig | None = None,
    ) -> None:
        self._facade = facade
        self._queue = queue
        self._config = config or ObserverConfig()

    # ------------------------------------------------------------------ drain

    def drain(self) -> WorkerReport:
        """Process all pending events; return the drain report."""
        pending = self._queue.pending()
        report = WorkerReport(events_read=len(pending))
        by_session: dict[str, list[tuple[int, Envelope]]] = defaultdict(list)
        for event_id, env in pending:
            by_session[env.session_id].append((event_id, env))
        for session_id, events in by_session.items():
            self._process_session(session_id, events, report)
        return report

    # ------------------------------------------------------------- processing

    def _process_session(
        self,
        session_id: str,
        events: list[tuple[int, Envelope]],
        report: WorkerReport,
    ) -> None:
        """Group a session's events into turns (by prompt_number) and process each."""
        turns: dict[int, list[tuple[int, Envelope]]] = defaultdict(list)
        for event_id, env in events:
            turns[env.prompt_number].append((event_id, env))
        for prompt_number in sorted(turns):
            self._process_turn(session_id, prompt_number, turns[prompt_number], report)

    def _process_turn(
        self,
        session_id: str,
        prompt_number: int,
        turn_events: list[tuple[int, Envelope]],
        report: WorkerReport,
    ) -> None:
        """Render one turn and write it as an episode (or skip it)."""
        prompt: str | None = None
        tool_events: list[dict[str, Any]] = []
        for _event_id, env in turn_events:
            if env.event_type == EVENT_USER_PROMPT_SUBMIT:
                prompt = env.payload.get("prompt", "")
            elif env.event_type == EVENT_POST_TOOL_USE:
                tool_name = env.payload.get("tool_name", "")
                if should_skip_event(tool_name, skip_tools=self._config.skip_tools):
                    report.events_skipped += 1
                    continue
                if should_drop_event(tool_name, drop_tools=self._config.drop_tools):
                    report.events_dropped += 1
                    continue
                tool_events.append(env.payload)

        if not prompt:
            # §4.3: a turn without a user prompt is not an episode.
            report.turns_skipped += 1
            self._ack_all(turn_events)
            return

        tag = short_session_tag(session_id)
        body = render_turn_body(
            prompt,
            tool_events,
            session_tag=tag,
            prompt_number=prompt_number,
            max_chars=self._config.body_max_chars,
        )
        if len(body) < self._config.min_body_chars:
            # §4.3: body < 40 chars → no episode.
            report.turns_skipped += 1
            self._ack_all(turn_events)
            return

        payload = RememberPayload(
            body=body,
            by={
                "source_type": "agent",
                "agent_id": self._agent_id(turn_events),
                "session_id": session_id,
            },
            title=build_title(prompt, tag, prompt_number),
            summary=derive_summary(body, max_chars=self._config.summary_max_chars),
            cognitive_type="episodic",
        )
        try:
            result = self._facade.remember(
                payload, extraction_mode=self._config.extraction_mode
            )
        except Exception:  # noqa: BLE001 — a failed turn must not kill the drain
            _logger.exception(
                "observe.worker: turn write failed session=%s turn=%s",
                session_id,
                prompt_number,
            )
            report.failures += 1
            return  # not acked → retried on the next drain
        if result.status == "COLLISION":
            report.collisions += 1
        else:
            report.episodes_written += 1
        self._ack_all(turn_events)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _agent_id(turn_events: list[tuple[int, Envelope]]) -> str:
        for _event_id, env in turn_events:
            if env.agent_id and env.agent_id != "unknown":
                return env.agent_id
        return "unknown"

    def _ack_all(self, turn_events: list[tuple[int, Envelope]]) -> None:
        for event_id, _env in turn_events:
            self._queue.ack(event_id)


__all__ = ["ObserverConfig", "ObserverWorker", "WorkerReport", "short_session_tag"]
