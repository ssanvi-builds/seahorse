"""Distillation layer (obsiforge §5) — deterministic consolidation.

A write-path operation over existing seams: ``consolidated`` is schema-valid,
``cognitive_type=semantic`` exists, and the consolidated episode references its
representative source via ``supersedes`` WITHOUT invalidating the sources. The
trigger is on-demand (``seahorse consolidate``); the session-end signal is OFF
by default (§15.2 redesign 5).
"""

from seahorse.distill.cluster import Cluster, cluster_episodes, cluster_key
from seahorse.distill.consolidate import (
    ConsolidateItem,
    ConsolidateReport,
    consolidate,
)
from seahorse.distill.distill import distill_episodes

__all__ = [
    "Cluster",
    "cluster_episodes",
    "cluster_key",
    "consolidate",
    "ConsolidateItem",
    "ConsolidateReport",
    "distill_episodes",
]
