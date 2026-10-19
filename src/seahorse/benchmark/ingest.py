"""Benchmark session ingest — the shared session-shape remember loop (C7).

Single-home of the ``session → turns → remember(skip)`` loop the SeahorseSUT
and ``lmeb_corpus.ingest_haystack`` once repeated: one payload builder
(``turn_payload``) and one loop (``remember_sessions`` — dedup by session_id,
oldest first, one remember per turn). Leaf module: imports ONLY facade types,
so both the SUT layer and the experiment layer can depend on it.

Deliberate scope: this helper tracks only ``ep_id → session``. The SUT's own
loop additionally feeds its ``SessionBridge`` (``fact_id → session`` +
``fact_key → ep_id`` for the KnowledgeUpdateSimulator); the standalone
session-level experiments never run that simulator, so a fact-shaped map here
would be dead weight.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from seahorse.facade.types import Provenance, RememberPayload

# Single-homed agent identity for synthesized benchmark provenance (the
# SeahorseSUT default matches, so both call sites emit the same by-field).
_BENCHMARK_AGENT_ID = "seahorse-benchmark"

_MIN_DT = datetime.min.replace(tzinfo=UTC)


def turn_payload(
    turn: Mapping[str, Any],
    session_id: str,
    *,
    temporal: bool = False,
    valid_at: datetime | None = None,
    agent_id: str = _BENCHMARK_AGENT_ID,
) -> RememberPayload:
    """One benchmark turn → the ``RememberPayload`` the SUT ingests.

    ``source_type='human'`` + ``valid_at`` in temporal mode (a human source
    permits an arbitrary valid_at; the guard forces skip); agent source and no
    ``valid_at`` otherwise. The session's date rides in via ``valid_at`` (the
    turn mapping carries no date). ``summary``/``title`` pass through
    verbatim (absent keys are ``None``).
    """
    return RememberPayload(
        body=turn["body"],
        by=Provenance(
            source_type="human" if temporal else "agent",
            agent_id=agent_id,
            session_id=session_id,
        ),
        valid_at=valid_at if temporal else None,
        title=turn.get("title"),
        summary=turn.get("summary"),
    )


def remember_sessions(
    facade: Any,
    sessions: Iterable[Mapping[str, Any]],
    *,
    temporal: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Ingest haystack sessions (skip mode); return ``(ep_ids, ep_id_to_session)``.

    Sessions arriving as one session per instance (every LMEB question carries
    its own haystack) are deduplicated by ``session_id`` (first wins) and
    ordered oldest-first so a fact's first version precedes its successor.
    Returns one entry per stored turn in ``ep_id_to_session``.
    """
    seen: set[str] = set()
    unique: list[Mapping[str, Any]] = []
    for session in sessions:
        sid = session["session_id"]
        if sid in seen:
            continue
        seen.add(sid)
        unique.append(session)
    ordered = sorted(unique, key=lambda s: s.get("date") or _MIN_DT)

    ep_ids: list[str] = []
    ep_id_to_session: dict[str, str] = {}
    for session in ordered:
        session_id = session["session_id"]
        date = session.get("date")
        for turn in session.get("turns", []):
            payload = turn_payload(
                cast(Mapping[str, Any], turn),
                session_id,
                temporal=temporal,
                valid_at=date,
            )
            wr = facade.remember(payload, skip_extraction=True)
            if wr.ep_id is not None:
                ep_ids.append(wr.ep_id)
                ep_id_to_session[wr.ep_id] = session_id
    return ep_ids, ep_id_to_session
