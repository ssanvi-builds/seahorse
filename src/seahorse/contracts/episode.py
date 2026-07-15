"""Episode — F3.1 root dataclass.

Owned by #1 (schema authority). Materialized here by #6 as the minimal frozen
shape #6 needs to compile and persist. When #1 ships the full Pydantic model it
IMPORTS and replaces the body IF the field set is a superset (SO-2 contract). It
must NOT add a required field that #6 did not materialize; all fields here
default to None except the F3.1 non-nullables (id, created_at, schema_version,
provenance).

References:
- f5-06 §3.2 (episodes DDL — the storage columns)
- f5-02 §3 (F3.1 schema)
- f6-signoffs.md SO-2 (fact_id = SHA-256(subject)[:32], subject = title>H1>None)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Episode:
    """F3.1 root episode. Immutable. #6 persists this; #2 derives fact_id/subject.

    ``body`` is the full markdown body (F3.1 first-class, excluded from the
    collision index, NOT NULL in the DDL). ``body_md`` is the storage column
    name; the Episode object carries ``body``. ``provenance`` is a dict
    serialized to JSON for storage.
    """

    id: str
    created_at: datetime
    schema_version: str
    provenance: dict[str, Any]
    body: str  # NOT NULL in DDL (body_md); F3.1 first-class content, never None
    subject: str | None = None
    fact_id: str | None = None
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    expired_at: datetime | None = None
    supersedes: str | None = None
    cognitive_type: str | None = None
    source_type: str | None = None
    title: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)

    def provenance_json(self) -> str:
        """Serialize provenance for the TEXT column with ``json_valid`` CHECK."""
        return json.dumps(self.provenance, sort_keys=True, separators=(",", ":"))
