"""Progressive Disclosure.

Projects the fused ranked list from Hybrid Retrieval into three disclosure
levels of increasing cost: ``index`` (1st call, ~50 tok/result, no body) →
``timeline`` (2nd call, anchor-based, no body) → ``full`` (3rd call, the only
level that hydrates ``body_md``). This module SHAPES; it does not fuse or rank.
The transition is upgrade-on-demand: the agent escalates explicitly with an
``anchor_ep_id`` (no heuristic auto-promotion; no LLM in the query path).

The payload shapes and transition protocol (``IndexRow``, ``TimelineEntry``,
``TimelineWindow``, ``FullDetail``, ``PITPoint``, ``TimelineAxis``,
``DisclosureShaper``) live in ``seahorse.disclosure.types`` / ``.shaper``.

The current release ships index + timeline (``supersedes_chain``/
``fact_id_scope``) + full; PIT-aware index/timeline (client-side composition
over ``chain_rows_from``); ``materialize_full`` with ``pit`` raises
``PitFullNotSupported``. Later-release axes (``created_at``/``valid_at``/
``graph_bfs``) raise ``NotInMVP0``.
"""

from __future__ import annotations

__all__: list[str] = []