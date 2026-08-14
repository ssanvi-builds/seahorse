"""Canonical body hash for importer idempotency.

Owned by the claude-mem importer, materialized here by the engine. Body-only
SHA-256 hex 64. Frontmatter is EXCLUDED — the caller passes the body only —
because frontmatter carries Engine-owned timestamps (``created_at``,
``expired_at``) that change between runs; including them would break re-import
idempotency.
"""

from __future__ import annotations

import hashlib
import unicodedata


def canonical_body_hash(body: str) -> str:
    """Return the SHA-256 hex 64 of the normalized body.

    Normalization: NFC unicode + strip trailing whitespace per line + collapse
    3+ blank lines to 2 + strip trailing newlines. Encoding: UTF-8.
    """
    normalized = unicodedata.normalize("NFC", body)
    lines = [line.rstrip() for line in normalized.split("\n")]
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run < 3:
                collapsed.append(line)
        else:
            blank_run = 0
            collapsed.append(line)
    canonical = "\n".join(collapsed).rstrip("\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()