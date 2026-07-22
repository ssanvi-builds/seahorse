"""Collision detection for I11 (owned by #2).

Two vigente episodes of the same derived subject are a detectable collision
(I11) UNLESS they share a ``supersedes`` chain (revalidate / improve within the
chain is not a concurrent collision). Detection is fail-loud awareness only:
the caller (``apply_fact`` / ``improve``) decides whether to skip or raise.

Subject derivation (SO-2 2d): ``title > first H1 > None`` normalized with NFC +
casefold + strip + whitespace collapse. ``fact_id = SHA-256(subject)[:32]``
(128-bit hex). ``Collision`` is an Engine-internal type (not a frontier symbol).

The subject-derivation primitives (``raw_subject``, ``normalize_subject``,
``fact_id_of``) are owned by ``seahorse.frontmatter.subject`` (#3, the
syntactic, file-aware derivation with the filename-stem fallback). The engine
has no ``path``, so it wraps them into a body-only signature that returns
``None`` when no title/H1 is found (the engine's "no subject → not indexed"
contract). No derivation logic is duplicated — the engine composes the same
primitives. ``fact_id_for`` stays here: it is the semantic fact-id (owned by #2)
that hashes the derived subject.
"""

from __future__ import annotations

from dataclasses import dataclass

from seahorse.contracts.engine import EpisodeRepository
from seahorse.contracts.episode import Episode
from seahorse.frontmatter.subject import (
    fact_id_of,
    normalize_subject,
    raw_subject,
)


@dataclass(frozen=True)
class Collision:
    """Engine-internal collision record (never crosses the contracts frontier)."""

    kind: str  # "concurrent"
    existing_id: str
    fact_id: str


def derive_subject(body: str, title: str | None = None) -> str | None:
    """Engine view: ``title > first H1 > None`` (no filename fallback).

    The engine has no ``path``, so it cannot fall back to a filename stem. When
    neither a non-empty ``title`` nor an H1 is present, this returns ``None`` and
    ``fact_id_for`` returns ``None`` — the episode is not indexed by subject.
    """
    raw = raw_subject(title, body)
    if raw is None:
        return None
    normalized = normalize_subject(raw)
    return normalized or None


def fact_id_for(body: str, title: str | None = None) -> str | None:
    """Return ``SHA-256(subject)[:32]`` hex, or ``None`` when no subject derivable."""
    subject = derive_subject(body, title=title)
    if subject is None:
        return None
    return fact_id_of(subject)


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