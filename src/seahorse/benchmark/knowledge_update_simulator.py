"""``KnowledgeUpdateSimulator`` — materializes ``supersedes`` chains (f5-16 §4.6).

Without an explicit step that creates ``supersedes`` chains via #12.improve,
there are no invalidated episodes to retrieve, and ``knowledge_update_accuracy``
+ FAMA are not computable.

OQ-16-13 (closed): the simulator DERIVES the ``(fact_key, old_version, new_version)``
pairs from the haystack when the adapter does not expose them explicitly — turns
sharing a ``fact_key`` across sessions, ordered by date, form update pairs. The
old version's ep_id is resolved from the SUT's ``fact_key_to_ep_id`` map (the
first ingested version); if absent, the old version is ingested fresh.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.benchmark.contracts import BenchmarkDataset, MemorySystemSUT
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT

_MIN_DT = datetime.min.replace(tzinfo=UTC)


class KnowledgeUpdateSimulator:
    """Detects facts that change between sessions and creates supersedes chains."""

    def __init__(self, sut: SeahorseSUT) -> None:
        self._sut = sut

    def derive_updates(self, dataset: BenchmarkDataset) -> dict[str, list[dict]]:
        """For each knowledge-update question, produce the update pairs.

        Uses the adapter-provided ``knowledge_updates`` when present; otherwise
        derives them from the haystack (OQ-16-13). Each pair carries
        ``(fact_key, old_ep_id, old_body, new_body, session_id, date)``.
        """
        updates: dict[str, list[dict]] = {}
        for inst in dataset.instances:
            if "knowledge-update" not in inst.capabilities:
                continue
            pairs = [dict(u) for u in inst.knowledge_updates]
            if not pairs:
                pairs = self._derive_from_haystack(inst)
            for p in pairs:
                if p.get("old_ep_id") is None:
                    key = p.get("fact_key")
                    if isinstance(key, str):
                        p["old_ep_id"] = self._sut.fact_key_to_ep_id.get(key)
            if pairs:
                updates[inst.instance_id] = pairs
        return updates

    def _derive_from_haystack(self, inst) -> list[dict]:
        """Derive update pairs from turns sharing a fact_key across sessions.

        Turns with the same ``fact_key`` are grouped; the earliest version is
        the old one, the latest is the new one (OQ-16-13).
        """
        by_key: dict[str, list[dict]] = {}
        for session in inst.haystack:
            date = session.get("date")
            session_id = session["session_id"]
            for turn in session.get("turns", []):
                key = turn.get("fact_key")
                if key is None:
                    continue
                by_key.setdefault(key, []).append(
                    {**turn, "session_id": session_id, "date": date}
                )
        pairs: list[dict] = []
        for key, versions in by_key.items():
            versions.sort(key=lambda v: v["date"] or _MIN_DT)
            if len(versions) < 2:
                continue
            old, new = versions[0], versions[-1]
            pairs.append(
                {
                    "fact_key": key,
                    "old_ep_id": self._sut.fact_key_to_ep_id.get(key),
                    "old_body": old["body"],
                    "new_body": new["body"],
                    "session_id": new["session_id"],
                    "date": new["date"],
                }
            )
        return pairs

    def apply(self, sut: MemorySystemSUT, updates: dict[str, list[dict]]) -> dict[str, list[str]]:
        """Apply the updates via #12.improve; return {instance_id: [new_ep_ids]}.

        When ``old_ep_id`` is None, the old version is ingested fresh first
        (a session with the old body). The new ep_ids are tracked for
        ``knowledge_update_accuracy`` (f5-16 §4.5).
        """
        result: dict[str, list[str]] = {}
        for inst_id, inst_updates in updates.items():
            new_ep_ids: list[str] = []
            for u in inst_updates:
                if u.get("old_ep_id") is None:
                    session = {
                        "session_id": u["session_id"],
                        "date": u.get("date"),
                        "turns": [{"body": u["old_body"]}],
                    }
                    ep_ids = sut.ingest([session])
                    u["old_ep_id"] = ep_ids[0] if ep_ids else None
                if u.get("old_ep_id") is None:
                    continue  # could not resolve the old version — skip
                new_ep_ids.extend(sut.apply_knowledge_updates([u]))
            result[inst_id] = new_ep_ids
        return result


__all__ = ["KnowledgeUpdateSimulator"]
