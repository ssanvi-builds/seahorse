"""Subject derivation — syntactic, from the file.

Part of the frontmatter migrator. ``derive_subject`` reads the file's own signals
in priority order ``title > first H1 > filename stem`` and normalizes with NFC +
casefold + strip + whitespace collapse. The filename-stem fallback is an addition
over the engine's body-only view: on the read/migrate path the file IS available,
so a note with neither title nor H1 still gets a non-empty, unique-ish subject.

The bi-temporal engine has no ``path`` (it operates on bodies), so
``engine/collision.py`` imports the shared helpers (``raw_subject``,
``normalize_subject``, ``fact_id_of``) and exposes a body-only wrapper that
returns ``None`` when no title/H1 is found — preserving the engine's "no
subject → not indexed" contract. No logic is duplicated: the engine wrapper
composes the same primitives.

``fact_id_of(subject) = SHA-256(subject)[:32]`` (128-bit hex). Truncation
birthday bound ~2^64, acceptable for a single vault.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

# First ATX H1 heading: a line starting with a single '#' then content.
# Non-greedy to the line end so trailing whitespace is trimmed by the capture.
# Use [ \t] (NOT \s) so the separator/trailing classes cannot consume a newline —
# otherwise a bare '#' or '# ' line followed by prose matches the prose as the H1
# content (Python \s includes '\n'), silently producing a wrong subject/fact_id.
_H1_RE = re.compile(r"(?m)^#[ \t]+(.+?)[ \t]*$")


def normalize_subject(raw: str) -> str:
    """NFC + casefold + strip + collapse internal whitespace."""
    s = unicodedata.normalize("NFC", raw).casefold().strip()
    return re.sub(r"\s+", " ", s)


def raw_subject(title: str | None, body: str) -> str | None:
    """``title > first H1 > None`` (no filename fallback).

    Returns the un-normalized source string, or ``None`` when neither a non-empty
    ``title`` nor an H1 heading is present. Callers normalize + decide the
    fallback (the engine returns ``None``; the frontmatter adapter falls back to
    ``path.stem``).

    Reconciliation with the design spec (code real > doc stale): the spec's
    pseudocode uses ``if not raw:`` (truthy check) on ``raw = fm_title``, which
    would keep a whitespace-only title (``"   "`` is truthy), then normalize it
    to ``""`` and route it to the empty-subject error (``E_SUBJECT_EMPTY``).
    This implementation instead treats a whitespace-only title as absent
    (``title.strip()``) and falls through to the H1 / filename stem — the more
    useful behaviour for a real vault, and the one the tests pin. Documented
    here as the third reconciliation alongside the adapter's body-separator and
    merge-preserves-unchanged notes.
    """
    if title is not None and title.strip():
        return title
    match = _H1_RE.search(body)
    if match is None:
        return None
    return match.group(1)


def derive_subject(title: str | None, body: str, path: Path) -> str:
    """``title > first H1 > path.stem``, normalized.

    Always returns a (possibly empty) string: the filename-stem fallback means a
    note with no title and no H1 still derives a subject. An empty result means a
    degenerate filename (e.g. ``.md``); the migrator treats that as case D
    (``E_SUBJECT_EMPTY``) and refuses to migrate.
    """
    raw = raw_subject(title, body)
    if raw is None:
        raw = path.stem
    return normalize_subject(raw)


def fact_id_of(subject: str) -> str:
    """``SHA-256(subject)[:32]`` hex (128-bit). The canonical fact-id derivation."""
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]