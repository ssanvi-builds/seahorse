"""Collision detection for I11 (owned by #2).

Two vigente episodes of the same derived subject are a detectable collision
(I11) UNLESS they share a ``supersedes`` chain (revalidate / improve within the
chain is not a concurrent collision). Detection is fail-loud awareness only:
the caller (``apply_fact`` / ``improve``) decides whether to skip or raise.

Subject derivation (SO-2 2d): ``title > first H1 > None`` normalized with NFC +
casefold + strip + whitespace collapse. ``fact_id = SHA-256(subject)[:32]``
(128-bit hex). ``Collision`` is an Engine-internal type (not a frontier symbol).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from seahorse.contracts.engine import EpisodeRepository
from seahorse.contracts.episode import Episode

# First ATX H1 heading: a line starting with a single '#' then whitespace.
_H1_RE = re.compile(r"(?m)^#\s+(.+)$")


@dataclass(frozen=True)
class Collision:
    """Engine-internal collision record (never crosses the contracts frontier)."""

    kind: str  # "concurrent"
    existing_id: str
    fact_id: str


def derive_subject(body: str, title: str | None = None) -> str | None:
    """Return the normalized subject: ``title > first H1 > None``."""
    raw: str | None = None
    if title is not None and title.strip():
        raw = title
    else:
        match = _H1_RE.search(body)
        if match is None:
            return None
        raw = match.group(1)
    normalized = unicodedata.normalize("NFC", raw).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized or None


def fact_id_for(body: str, title: str | None = None) -> str | None:
    """Return ``SHA-256(subject)[:32]`` hex, or ``None`` when no subject derivable."""
    subject = derive_subject(body, title=title)
    if subject is None:
        return None
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]


class CollisionDetector:
    """Detect I11 collisions of a candidate episode against the vigente set."""

    @staticmethod
    def derive_subject(body: str, title: str | None = None) -> str | None:
        return derive_subject(body, title=title)

    @staticmethod
    def fact_id_for(body: str, title: str | None = None) -> str | None:
        return fact_id_for(body, title=title)

    def detect(self, new_ep: Episode, repo: EpisodeRepository) -> list[Collision]:
        fact_id = fact_id_for(new_ep.body or "", title=new_ep.title)
        if fact_id is None:
            return []
        other = repo.find_vigent_by_fact_id(fact_id, exclude=new_ep.id)
        if other is None:
            return []
        # Same supersedes chain (revalidate / improve within the chain) is not a
        # concurrent collision: the new episode continues the lineage, not a rival.
        if new_ep.supersedes is not None:
            chain_ids = {e.id for e in repo.chain_from(new_ep.supersedes)}
            if other.id in chain_ids:
                return []
        return [Collision(kind="concurrent", existing_id=other.id, fact_id=fact_id)]