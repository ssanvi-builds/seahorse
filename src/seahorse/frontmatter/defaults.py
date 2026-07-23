"""Safe MVP-0 migration defaults (f5-03 §3.2).

Owned by #3. The migrator builds a fresh F3.1 ``Episode`` for a legacy note
(case A: no frontmatter; case B: non-F3.1 frontmatter) from safe defaults, not
from extraction (#5) — MVP-0 is additive, ``extraction_mode=skip`` (ADR-09).

``valid_at = created_at = file_mtime`` (UTC) by construction, so the skip-path
border contract (``valid_at <= created_at`` when ``valid_at`` is not null,
f5-03 §5.7) always holds. A suspicious ``file_mtime`` (far future, epoch 0)
falls back to ``now`` UTC and the caller records a *timestamp fallback*
collision in ``migration_collisions.log`` (f5-03 §3.1).

``new_uuid7`` is reused from ``seahorse.facade`` (stdlib-synthesized in
``engine.ids``); no ``uuid_extensions`` dependency (plan 5.7).
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.episode import Episode
from seahorse.facade import new_uuid7

# MVP-0 schema version (f5-03 §3.2). Bump only on a wire-breaking schema change.
SCHEMA_VERSION_MVP0 = "0.1.0"
# The agent_id the migrator stamps into provenance (f5-03 §3.2).
MIGRATOR_AGENT_ID = "seahorse/migrator"
# The migrator version written to the manifest (f5-03 §3.5).
MIGRATOR_VERSION = "seahorse-f3.3-0.1.0"
# The manifest format version (f5-03 §3.5).
MANIFEST_VERSION = "0.1.0"

# A file mtime is "suspicious" when it is implausible as the note's real write
# time: epoch-0 (1970) or far-future (> 1 day ahead). Both trigger the
# *timestamp fallback* (use ``now`` UTC + log a collision) per f5-03 §3.1.
_SUSPICIOUS_FUTURE_MARGIN = 24 * 3600  # seconds
_EPOCH_CUTOFF = datetime(2000, 1, 1, tzinfo=UTC)


def is_suspicious_mtime(file_mtime: datetime, *, now: datetime) -> bool:
    """True if ``file_mtime`` is implausible (far future or pre-2000 epoch 0)."""
    if file_mtime.tzinfo is None:
        # Treat naive mtime as suspicious (we require aware UTC everywhere).
        return True
    if file_mtime > now.replace(microsecond=0) and (
        file_mtime - now
    ).total_seconds() > _SUSPICIOUS_FUTURE_MARGIN:
        return True
    return file_mtime < _EPOCH_CUTOFF


def migration_defaults(
    file_mtime: datetime, run_session_id: str, *, now: datetime | None = None
) -> tuple[Episode, list[str]]:
    """Build the safe MVP-0 episode for a legacy note (f5-03 §3.2).

    Returns ``(episode, collisions)``: the episode carries the F3.1 fields the
    migrator writes, and ``collisions`` lists any timestamp-fallback event
    (empty when ``file_mtime`` was used as-is). ``valid_at == created_at`` by
    construction. ``body`` is left ``None`` — the migrator writes the file's
    existing body verbatim, not the episode's body field.
    """
    collisions: list[str] = []
    ts = file_mtime
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC)
    reference_now = now if now is not None else datetime.now(UTC)
    if is_suspicious_mtime(file_mtime, now=reference_now):
        ts = reference_now.astimezone(UTC)
        collisions.append("timestamp fallback: suspicious file mtime")
    ep = Episode(
        id=new_uuid7(),
        created_at=ts,
        schema_version=SCHEMA_VERSION_MVP0,
        provenance={
            "agent_id": MIGRATOR_AGENT_ID,
            "session_id": run_session_id,
            "source_type": "human",
            "extraction_mode": "skip",
        },
        valid_at=ts,
        cognitive_type="semantic",
    )
    return ep, collisions