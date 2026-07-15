"""Bi-temporal Engine behavior layer (owned by #2).

Builds on top of the Persistence Layer (#6) via the ``EpisodeRepository``
Protocol. The contract frontier (``WriteResult``, ``FreshnessView``,
``EpisodeRepository``, ``AuditEvent``, ``NotFound``,
``InvalidationConflictError``) lives in ``seahorse.contracts.engine``; this
package imports those symbols, it does not relocate them.

MVP-0 behavior: ``apply_fact`` / ``remember`` / ``forget`` / ``improve`` +
readers (``get_vigente``, ``follow_supersedes_chain``, ``is_valid_at``,
``is_known_at``, ``audit_log``, ``freshness_view``), ``WriteGuards`` (I1-I11),
``CollisionDetector.detect`` (fail-loud). MVP-1 primitives
(``state_at``, ``recall_pit``, ``detect_collisions`` public,
``resolve_conflict``, ``revalidate``, ``expire``, ``DefaultConflictPolicyMVP1``)
raise ``EngineError("E_NOT_IN_MVP_0")``.

References:
- f5-02 (bi-temporal engine design)
- f6-signoffs.md SO-3 (apply_fact fail-loud, AuditEvent store)
- f6-signoffs.md SO-4 (importer I1/I2 amendments)
- f6-signoffs.md SO-8c (WriteResult(ep_id, fact_id))
"""

from __future__ import annotations

from seahorse.engine.engine import BiTemporalEngine

__all__ = ["BiTemporalEngine"]