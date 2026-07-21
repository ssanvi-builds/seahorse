"""Episode — F3.1 root model (Pydantic v2).

Owned by #1 (schema authority). Materialized by #6 as the minimal frozen shape
#6 needs to compile and persist. #3 (frontmatter adapter/migrator) is the
forcing function that ships the canonical Pydantic model and migrates #6 to
import it, so a single ``Episode`` type is shared system-wide.

SO-2 contract: the field set is a SUPERSET of what #6 materialized — no required
field that #6 did not have. The F3.1 non-nullables (``id``, ``created_at``,
``schema_version``, ``provenance``) are required (no default) and typed
non-``None``: this matches the DDL (NOT NULL) and runtime reality. ``body`` is
Optional (``str | None = Field(default=None, exclude=True)``) per f5-03 §5.8: it
is NOT serialized to the YAML frontmatter (``exclude=True``), so ``parse_file``
constructs an Episode from frontmatter WITHOUT body and ``hydrate`` attaches it
lazily via ``model_copy(update={"body": body})``. The DDL NOT NULL on ``body_md``
is enforced at storage write, not at model construction. The bi-temporal
timestamps (``valid_at`` / ``invalid_at`` / ``expired_at``) and the index hints
(``subject`` / ``fact_id`` / ``title`` / ``summary`` / ``cognitive_type`` /
``source_type`` / ``supersedes`` / ``supersedes_reason``) default to ``None``.
``supersedes_reason`` is the portable frontmatter key (f5-03 §12.3): it lives on
the model and travels the wire from commit 1, but its SQLite column lands in
commit 4 (migration 009) — until then it is model+wire only, not yet persisted by
``SqliteEpisodeRepository``.

Permissive storage model: the model only enforces types + ``frozen`` +
``extra="allow"`` + the ISO-8601 UTC ``Z`` serializers. The strict F3.1
write-time validators (naive-datetime rejection, expired-null MVP-0, UUIDv7
shape, self-supersede) are deferred to commit 2, where
``frontmatter/schema.py::validate_for_write`` wires them via
``model_validate(..., context={"mvp": ...})`` (f5-03 §4.1/§7.2). The engine guards
(I1-I11) enforce bi-temporal invariants at write time regardless. The model is
intentionally permissive so the existing tests keep constructing episodes with
``expired_at`` non-null (guard tests), ``id="e1"`` (fixtures), and
``supersedes == id`` self-loops (chain-traversal cycle tests). Tests that
exercise a guard on a missing ``created_at`` build the episode via
``model_copy(update={"created_at": None})``, which skips validation (Pydantic-
idiomatic for "construct an instance that violates the schema, to test the guard
that catches it").

Pydantic lives in the core (``contracts`` → ``engine``/``persistence``/
``facade``/``mcp`` transitively). This relaxes the stdlib-only-core contract;
accepted deviation (F4 recommends Pydantic v2 canonical; B1 forces it). Only
``ruamel.yaml`` + ``python-frontmatter`` stay confined to ``seahorse/frontmatter/``.

References:
- f5-06 §3.2 (episodes DDL — the storage columns)
- f5-02 §3 (F3.1 schema)
- f6-signoffs.md SO-2 (fact_id = SHA-256(subject)[:32], subject = title>H1>None)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class Episode(BaseModel):
    """F3.1 root episode. Immutable (``frozen``). #6 persists; #2 derives subject/fact_id.

    ``body`` is the full markdown body (F3.1 first-class, excluded from the
    serialized YAML shape via ``exclude=True`` — the wire serializers in #13/#14
    read it via ``getattr`` so it still travels the wire, but ``model_dump``
    omits it). ``body`` is Optional: ``parse_file`` (#3) constructs an Episode
    from frontmatter WITHOUT body and ``hydrate`` attaches it lazily via
    ``model_copy``; the DDL NOT NULL on ``body_md`` is enforced at storage write.
    Call sites that need a non-None body (``fact_id_for``/``derive_subject``/
    ``canonical_body_hash``/rendering) coerce with ``ep.body or ""`` (f5-03 §5.8
    ``serialize``), a no-op on the engine write path where body is always supplied.
    ``provenance`` is a freeform dict serialized to JSON for storage (SO-2: not a
    sub-model — #6 uses freeform dicts).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    id: str
    created_at: datetime
    schema_version: str
    provenance: dict[str, Any]
    body: str | None = Field(default=None, exclude=True)
    subject: str | None = Field(default=None, exclude=True)
    fact_id: str | None = Field(default=None, exclude=True)
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    expired_at: datetime | None = None
    supersedes: str | None = None
    supersedes_reason: str | None = None
    cognitive_type: str | None = None
    source_type: str | None = None
    title: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_serializer("created_at", "valid_at", "invalid_at", "expired_at")
    def _z(self, dt: datetime | None) -> str | None:
        """Canonical ISO-8601 UTC with ``Z`` suffix (F3.1 §4) for ``model_dump``.

        The wire serializers (#13/#14) canonicalize independently via
        ``getattr`` + ``_iso_z``; this serializer governs the YAML round-trip
        used by the frontmatter adapter (#3).
        """
        if dt is None:
            return None
        aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def provenance_json(self) -> str:
        """Serialize provenance for the TEXT column with ``json_valid`` CHECK."""
        return json.dumps(self.provenance, sort_keys=True, separators=(",", ":"))