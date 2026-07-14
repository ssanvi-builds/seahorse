# Seahorse — Roadmap

> Canonical roadmap lives in the design vault: [`Roadmap/Progress.md`](../obsidian-vaults/seahorse/Roadmap/Progress.md) and [`Roadmap/Roadmap.md`](../obsidian-vaults/seahorse/Roadmap/Roadmap.md). This file is a thin execution mirror — no duplicated prose.

## Current phase

**Fase 6 — Implementation** (about to start).

- Fase 5 detailed design: COMPLETE (16/16 components, `f5-01`..`f5-16`).
- F6 sign-off sprint: COMPLETE (8/8 blocking decisions signed, ranks 1-8; ranks 9-15 close inline during F6). Register: `Claude/f6-signoffs.md`.
- Repo bootstrap: `3c655fe chore: initial scaffold` on `main` (LICENSE Apache-2.0, README, .gitignore, pyproject.toml).

## F6 execution order

Iterative, TDD (test first), 80% coverage minimum. Start with:

1. **#6 Persistence Layer** — materialize the co-finalized DDL of SO-3 (`episodes` + contract #2 indexes + `episode_index` + `episode_paths` + `vec0` + FTS5 external content + `audit_events`) and the SO-7 lateral DDLs (`vec_episodes_meta`, `embeddings_cache`, `reindex_jobs`) over SQLite WAL, single-writer/multi-reader, single connection (ADR-04).
2. **#2 Bi-temporal Engine** — `apply_fact` / episode lifecycle on top of #6, with the SO-8c correction (`WriteResult(ep_id, fact_id)`).

Protocols to land with #6: `EpisodeIndexRepository` (SO-1), `VectorIndexRepository` (SO-7, fold-into-upsert + `distinct_model_identities`), `EmbeddingsCacheRepository`, `ReindexJobRepository`.

## Deferred to F6 inline (ranks 9-15, not pre-signed)

- Uniform return type for `improve` / `forget`.
- `cognitive_type` enum alignment #13/#14 (CRITICAL per #14).
- F4 corrections folded into ADR-04 / ADR-05.
- `drain()` contract for #7 (OQ-16-12, synchronous embeddings barrier for #16).
- OQ-16-2 (`temperature` / `seed` in `complete()`); #16 starts with LiteLLM direct meanwhile.

## Promotion tasks (execute component-by-component at F6 start)

Editorial corrections to source docs + mark resolved OQ/Sign-offs:
- `f5-02` §5.4 and `f5-03` §5.6: unify subject rule to `title > first H1 > None` (SO-2).
- `f5-07` §1.3 vs §2.2: reconcile runtime PQ vs INT8 ONNX weights (SO-7).
- `stack-f4` §1.2: reconcile the "CTE" vs "RRF Python" wording (SO-6 amendment).

## License

Apache-2.0. The reference standard is open; the SaaS track is proprietary; enterprise self-host is BSL 1.1 → Apache (ADR-011). Acquisition-by-a-lab is explicitly not a goal.