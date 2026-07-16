# Seahorse — Roadmap

> Canonical roadmap lives in the design vault: [`Roadmap/Progress.md`](../obsidian-vaults/seahorse/Roadmap/Progress.md) and [`Roadmap/Roadmap.md`](../obsidian-vaults/seahorse/Roadmap/Roadmap.md). This file is a thin execution mirror — no duplicated prose.

## Current phase

**Fase 6 — Implementation** (in progress).

- Fase 5 detailed design: COMPLETE (16/16 components, `f5-01`..`f5-16`).
- F6 sign-off sprint: COMPLETE (8/8 blocking decisions signed, ranks 1-8; ranks 9-15 close inline during F6). Register: `Claude/f6-signoffs.md`.
- Repo bootstrap: `3c655fe chore: initial scaffold` on `main` (LICENSE Apache-2.0, README, .gitignore, pyproject.toml).
- **#6 Persistence Layer: COMPLETE** (2026-07-15). MVP-0 relacional + Protocols completos. 149 tests / coverage 96.56% / ruff+mypy clean / no runtime deps (stdlib `sqlite3` only). 9 commits on `main` (`858b41b`→`be05746`), not pushed (git-conservative). Frontier package `seahorse/contracts/` materializes the symbols of #1/#2/#8/#10 with `OWNED BY` headers — later components import from there, no breaking moves. See vault session note `session-2026-07-15-f6-06-persistence-layer.md`.
- **#2 Bi-temporal Engine: COMPLETE** (2026-07-15). MVP-0 behavior layer + MVP-1 stubs. `apply_fact` (SO-3b fail-loud) / `remember` (SO-4b UUIDv7+UUIDv5 idempotent) / `forget` (reason→audit only) / `improve` (I8 atomic invalidate-then-append + TD #8 fail-loud `E_COLLISION_EXISTS` on third-party collision + rollback) + 6 readers + `WriteGuards` I1–I11 + `CollisionDetector` + `is_valid_skip_path` (border #5). 7 MVP-1 stubs `E_NOT_IN_MVP_0` (`@mvp1_axis`). Frontier `contracts/engine.py` extended additively with `WriteResult(ep_id, fact_id, status, collisions_detected)` + `FreshnessView(fact_id: str | None, ...)`; subpackage `seahorse/engine/` (8 source files, 911 lines). TD #14 bridge equality `WriteResult.fact_id == IndexRow.fact_id` by construction. 306 tests / coverage 97.61% / ruff+mypy clean / no runtime deps (stdlib uuid/hashlib/sqlite3/threading/re/dataclasses). 5 commits on `main` (`193593c`→`e2c3085`, 14 accumulated), not pushed. Review: ~28-agent adversarial Workflow, 1 HIGH (apply_fact TOCTOU) caught+fixed (re-detect inside `repo.atomic()` + IntegrityError backstop + engine-level `RLock` per spec §8.9); E2E smoke caught improve successor-without-fact_id bug. See vault session note `session-2026-07-15-f6-02-bitemporal-engine.md`.
- **#8 Progressive Disclosure: COMPLETE** (2026-07-16). MVP-0 SHAPER — projects #11's fused list into 3 levels (INDEX ~50 tok no body / TIMELINE ≤20 no body / FULL ≤5 with body). `DisclosureShaper` Protocol + `DisclosureShaperImpl` over #6 (index) + #2 (episode): `materialize_index` (score passthrough, no reorder, no body, deterministic truncation ADR-10, `stale`/`pending_ingest` current-regime flags, PIT delegates to #6 typed accessors) · `materialize_timeline` (MVP-0 axes `supersedes_chain`/`fact_id_scope` only; MVP-1 → `NotInMVP0`; `score=None` always; PIT composed client-side over #6 accessors, no predicate drift ADR-03; `MAX_TIMELINE_WINDOW=20` cap) · `materialize_full` (only level hydrating body; `MAX_FULL_BATCH=5` → `FullBatchTooLarge`; `pit` → `PitFullNotSupported` MVP-0; typed provenance; freshness via shared `freshness_of` DRY with #2). Frontier: `contracts/retrieval.py` (`FusedCandidate` owned by #11, materialized by #8 — OWNED BY mirror of `IndexRowData`) + `contracts/engine.py` (`freshness_of` extracted as pure helper). Blocker `EpisodeIndexRepository` (pending in f5-08 §3.1) RESOLVED — materialized in F6 #6. 363 tests / coverage 97.87% / ruff+mypy clean / no runtime deps. 4 commits on `main` (`67bce98`→`d160dc3`, 18 accumulated), not pushed. Review: 11-agent adversarial Workflow, 7 raised / 4 confirmed (all test-enforcement gaps, no production bugs — shaper correct) / 3 refuted; closed with 10 tests + 2 recording doubles (`RecordingIndex`/`CountingEpisodeRepo`). Phase 0: f5-08 §3.1 + tech debt items 1/2/8 marked resolved. See vault session note `session-2026-07-16-f6-08-progressive-disclosure.md`.

## F6 execution order

Iterative, TDD (test first), 80% coverage minimum.

1. ✅ **#6 Persistence Layer** — COMPLETE 2026-07-15. SQLite WAL relational DDL (8 idempotent migrations: `episodes` + #2 indexes + `episode_index` + `episode_paths` + `audit_events` + 3 SO-7 laterals) over `ConnectionManager` (single writer RLock-reentrant + N WAL readers). 6 SQLite repos + `Storage` composition root with the single shared `atomic()` (SO-7a.6). Vector/FTS are signed Protocols with `NotImplementedError` stubs (MVP-1; no `sqlite-vec` runtime dep in MVP-0). Review: 28-agent adversarial Workflow, 0 CRITICAL/0 HIGH, 8 non-critical fixes applied.
2. ✅ **#2 Bi-temporal Engine** — COMPLETE 2026-07-15. MVP-0 behavior layer + MVP-1 stubs on top of #6. SO-8c `WriteResult(ep_id, fact_id)`; SO-3b fail-loud on collision; SO-4b UUIDv7/UUIDv5 idempotency; TD #8 improve fail-loud `E_COLLISION_EXISTS` + I8 atomic rollback; TD #14 bridge equality. Subpackage `seahorse/engine/` (8 files) + frontier extension `WriteResult`/`FreshnessView`. Review: ~28-agent Workflow, 1 HIGH (apply_fact TOCTOU) fixed.
3. ✅ **#8 Progressive Disclosure** — COMPLETE 2026-07-16. MVP-0 SHAPER projecting #11's fused list into INDEX/TIMELINE/FULL. `DisclosureShaperImpl` over #6 (index) + #2 (episode); PIT delegates to #6 typed accessors (no predicate drift ADR-03); `freshness_of` DRY with #2. Frontier `contracts/retrieval.py` (`FusedCandidate` OWNED BY #11) + `contracts/engine.py` (`freshness_of`). Blocker `EpisodeIndexRepository` RESOLVED (materialized in #6). Review: 11-agent Workflow, 4 test-enforcement gaps confirmed (no production bugs) + closed with recording doubles.
4. ▶️ **Next F6 component** — #8 unblocks #12 facade (recall MVP-0 G2 routes via `#8.materialize_index`) and de-risks #11/#9/#10 (they project over #8). Candidates: #12 Primitives Facade (now fully enabled — #2+#8 ready), #5 skip_extraction Write Path, #11 Hybrid Retrieval (MVP-1, `EpisodeIndexRepository` blocker resolved). Pre-work: Phase 0 promotions for the component in its `f5-xx` doc. TDD + 80% gate + adversarial Workflow pre-commit.

Protocols to land with #6: `EpisodeIndexRepository` (SO-1), `VectorIndexRepository` (SO-7, fold-into-upsert + `distinct_model_identities`), `EmbeddingsCacheRepository`, `ReindexJobRepository`.

## Deferred to F6 inline (ranks 9-15, not pre-signed)

- Uniform return type for `improve` / `forget`. ✅ RESOLVED in F6 #2 (TD #8, 2026-07-15): both preserve `-> Episode` + fail-loud `EngineError("E_COLLISION_EXISTS")` on third-party collision with I8 atomic rollback.
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