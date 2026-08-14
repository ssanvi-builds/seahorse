"""Safe first-release migration defaults.

Part of the frontmatter migrator. The migrator builds a fresh on-disk
``Episode`` for a legacy note (case A: no frontmatter; case B: non-on-disk
frontmatter) from safe defaults, not from extraction — the first release is
additive, ``extraction_mode=skip`` (the skip path).

``valid_at = created_at = file_mtime`` (UTC) by construction, so the skip-path
border contract (``valid_at <= created_at`` when ``valid_at`` is not null)
always holds. A suspicious ``file_mtime`` (far future, epoch 0)
falls back to ``now`` UTC and the caller records a *timestamp fallback*
collision in ``migration_collisions.log``.

``new_uuid7`` is reused from ``seahorse.facade`` (stdlib-synthesized in
``engine.ids``); no ``uuid_extensions`` dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.episode import Episode
from seahorse.facade import new_uuid7

# First-release schema version. Bump only on a wire-breaking schema change.
SCHEMA_VERSION_MVP0 = "0.1.0"
# The agent_id the migrator stamps into provenance.
MIGRATOR_AGENT_ID = "seahorse/migrator"
# The migrator version written to the manifest.
MIGRATOR_VERSION = "seahorse-f3.3-0.1.0"
# The manifest format version.
MANIFEST_VERSION = "0.1.0"

# A file mtime is "suspicious" when it is implausible as the note's real write
# time: epoch-0 (1970) or far-future (> 1 day ahead). Both trigger the
# *timestamp fallback* (use ``now`` UTC + log a collision).
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
    """Build the safe first-release episode for a legacy note.

    Returns ``(episode, collisions)``: the episode carries the on-disk fields the
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