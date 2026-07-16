"""Progressive Disclosure (owned by #8).

Projects the fused ranked list from #11 Hybrid Retrieval into three disclosure
levels of increasing cost: ``index`` (1st call, ~50 tok/result, no body) →
``timeline`` (2nd call, anchor-based, no body) → ``full`` (3rd call, the only
level that hydrates ``body_md``). #8 SHAPES; it does not fuse or rank. The
transition is upgrade-on-demand: the agent escalates explicitly with an
``anchor_ep_id`` (no heuristic auto-promotion; no LLM in the query path).

The payload shapes and transition protocol (``IndexRow``, ``TimelineEntry``,
``TimelineWindow``, ``FullDetail``, ``PITPoint``, ``TimelineAxis``,
``DisclosureShaper``) live in ``seahorse.disclosure.types`` / ``.shaper``.

MVP-0: index + timeline (``supersedes_chain``/``fact_id_scope``) + full for
L2a; PIT-aware index/timeline (client-side composition over ``chain_rows_from``);
``materialize_full`` with ``pit`` raises ``PitFullNotSupported``. MVP-1 axes
(``created_at``/``valid_at``/``graph_bfs``) raise ``NotInMVP0``.

References:
- f5-08 (progressive disclosure design)
- f6-signoffs.md SO-1 (EpisodeIndexRepository — the typed accessor #8 consumes,
  materialized by #6; the blocker that previously gated #8 is now resolved)
"""

from __future__ import annotations

__all__: list[str] = []