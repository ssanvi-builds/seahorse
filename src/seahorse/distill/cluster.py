"""Deterministic clustering for distillation.

The clustering key is DISTINCT from the stored subject: the observer's H1
carries a ``[session_tag:prompt_number]`` suffix, so the stored subject is unique
per turn — the N≥3 recurrence trigger would NEVER fire if it clustered on the
stored subject. The key strips the tag suffix, so episodes about the same topic
cluster together. Deterministic: the same input always produces the same
clusters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The observer H1 suffix: ``[session_tag:prompt_number]``.
_TAG_SUFFIX_RE = re.compile(r"\s*\[[^\]]+:\d+\]\s*$")

# Recurrence threshold: N≥3 currently-valid episodes with the same clustering key.
MIN_CLUSTER_SIZE = 3


@dataclass(frozen=True)
class Cluster:
    """A group of episodes about the same topic (the distillation unit)."""

    key: str
    episodes: list[Any]
    representative: Any  # the most recent episode of the cluster


def cluster_key(subject: str) -> str:
    """The clustering key: the subject WITHOUT the ``[session_tag:n]`` suffix.

    Normalized to lowercase (the engine already normalizes subjects, but the
    key is derived defensively). Episodes about the same topic — across turns
    and sessions — share a key, so the N≥3 recurrence trigger can fire.
    """
    return _TAG_SUFFIX_RE.sub("", subject).strip().lower()


def cluster_episodes(episodes: list[Any], *, min_size: int = MIN_CLUSTER_SIZE) -> list[Cluster]:
    """Group currently-valid episodes by clustering key; return clusters with ≥N.

    The representative is the most recent episode of the cluster (deterministic
    tie-break: ``created_at`` desc, ``ep_id`` asc). Episodes without a subject
    are excluded (no key to cluster on).
    """
    by_key: dict[str, list[Any]] = {}
    for ep in episodes:
        if not ep.subject:
            continue
        key = cluster_key(ep.subject)
        by_key.setdefault(key, []).append(ep)

    clusters: list[Cluster] = []
    for key, eps in by_key.items():
        if len(eps) < min_size:
            continue
        # Deterministic order: created_at desc, ep_id asc.
        ordered = sorted(eps, key=lambda e: e.id)
        ordered = sorted(ordered, key=lambda e: e.created_at, reverse=True)
        clusters.append(Cluster(key=key, episodes=ordered, representative=ordered[0]))
    # Deterministic cluster order.
    clusters.sort(key=lambda c: c.key)
    return clusters


__all__ = ["Cluster", "cluster_key", "cluster_episodes", "MIN_CLUSTER_SIZE"]
