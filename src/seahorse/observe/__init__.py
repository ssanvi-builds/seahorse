"""Observer capture layer.

The write side of the capture/context/consolidate suite: harness hooks →
redacted envelopes → SQLite queue → deterministic batcher → ``RememberPayload``
via the facade. stdlib-only
(``http.server`` + ``sqlite3`` + ``json``), zero harness imports in the core —
the Claude Code adapter lives in ``observe/adapters/``.
"""

from seahorse.observe.protocol import (
    Envelope,
    EnvelopeError,
    parse_envelope,
)

__all__ = ["Envelope", "EnvelopeError", "parse_envelope"]
