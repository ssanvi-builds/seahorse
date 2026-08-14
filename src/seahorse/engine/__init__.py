"""Bi-temporal Engine behavior layer.

Builds on top of the Persistence Layer via the ``EpisodeRepository``
Protocol. The contract frontier (``WriteResult``, ``FreshnessView``,
``EpisodeRepository``, ``AuditEvent``, ``NotFound``,
``InvalidationConflictError``) lives in ``seahorse.contracts.engine``; this
package imports those symbols, it does not relocate them.

Current-release behavior: ``apply_fact`` / ``remember`` / ``forget`` / ``improve``
plus readers (``get_vigente``, ``follow_supersedes_chain``, ``is_valid_at``,
``is_known_at``, ``audit_log``, ``freshness_view``), the write-time guards,
``CollisionDetector.detect`` (fail-loud). Later-release primitives
(``state_at``, ``recall_pit``, ``detect_collisions`` public,
``resolve_conflict``, ``revalidate``, ``expire``, ``DefaultConflictPolicyMVP1``)
raise ``EngineError("E_NOT_IN_MVP_0")``.
"""

from __future__ import annotations

from seahorse.engine.engine import BiTemporalEngine

__all__ = ["BiTemporalEngine"]