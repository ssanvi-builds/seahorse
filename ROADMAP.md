# Seahorse — Roadmap

> Canonical roadmap lives in the design vault: [`Roadmap/Progress.md`](../obsidian-vaults/seahorse/Roadmap/Progress.md) and [`Roadmap/Roadmap.md`](../obsidian-vaults/seahorse/Roadmap/Roadmap.md). This file is a thin execution mirror — no duplicated prose.

## Current phase

**Fase 6 — Implementation** (in progress).

- Fase 5 detailed design: COMPLETE (16/16 components, `f5-01`..`f5-16`).
- F6 sign-off sprint: COMPLETE (8/8 blocking decisions signed, ranks 1-8; ranks 9-15 close inline during F6). Register: `Claude/f6-signoffs.md`.
- Repo bootstrap: `3c655fe chore: initial scaffold` on `main` (LICENSE Apache-2.0, README, .gitignore, pyproject.toml).
- **#6 Persistence Layer: COMPLETE** (2026-07-15). MVP-0 relacional + Protocols completos. 149 tests / coverage 96.56% / ruff+mypy clean / no runtime deps (stdlib `sqlite3` only). 9 commits on `main` (`858b41b`→`be05746`), not pushed (git-conservative). Frontier package `seahorse/contracts/` materializes the symbols of #1/#2/#8/#10 with `OWNED BY` headers — later components import from there, no breaking moves. See vault session note `session-2026-07-15-f6-06-persistence-layer.md`.

## F6 execution order

Iterative, TDD (test first), 80% coverage minimum.

1. ✅ **#6 Persistence Layer** — COMPLETE 2026-07-15. SQLite WAL relational DDL (8 idempotent migrations: `episodes` + #2 indexes + `episode_index` + `episode_paths` + `audit_events` + 3 SO-7 laterals) over `ConnectionManager` (single writer RLock-reentrant + N WAL readers). 6 SQLite repos + `Storage` composition root with the single shared `atomic()` (SO-7a.6). Vector/FTS are signed Protocols with `NotImplementedError` stubs (MVP-1; no `sqlite-vec` runtime dep in MVP-0). Review: 28-agent adversarial Workflow, 0 CRITICAL/0 HIGH, 8 non-critical fixes applied.
2. ▶️ **#2 Bi-temporal Engine** — `apply_fact` / episode lifecycle on top of #6, with the SO-8c correction (`WriteResult(ep_id, fact_id)`). Consumes `seahorse.contracts` + #6 repos without relocating symbols. Pre-work: Phase 0 promotions touching #2 (SO-3 `apply_fact` fail-loud, SO-8c) in `f5-02`.

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