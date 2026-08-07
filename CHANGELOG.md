# Changelog

All notable changes to Seahorse are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **F7 skeleton #16 — LMEB benchmark harness (MVP-1 scope, f5-16 §7.1)** —
  `seahorse/benchmark/`: contracts + config (`BenchmarkConfig.validate()`
  rejects `reader_model == judge_model`), metrics (Recall@k, nDCG@k binary,
  MRR, Precision@k with `k_effectivo`, FAMA-gap sanity check,
  knowledge_update_accuracy, REAL token efficiency, p95 latency per level),
  `SeahorseSUT` (#12→SUT adapter with the `fact_id→session` bridge + honest
  `fallback_g2` detection + F7 flags `recency_config`/`rerank_enabled`/
  `embed_mode`), `CorpusBuilder` (skip-mode, deterministic `AdvancingClock`),
  `KnowledgeUpdateSimulator` (OQ-16-13: derives update pairs from the
  haystack), `EvaluationRunner` + `LevelProbeRunner` (isolated p95
  TIMELINE/FULL without the reader LLM), reporters (bit-comparable
  `PinningFingerprint` + separate `ExecutionMetadata`, json/markdown/ci_gate
  exit 0/10/3), `LMEBLoader` + `AdapterRegistry`, harness (ReaderLLMClient
  t=0 seed=42, Tokenizer tiktoken, git-tracked prompts) + `LLMJudge` (bias
  mitigations: generator≠judge, position swap, strict+lenient), and
  reproducibility (`ModelPin`/`pin_ollama_model`, SQLite `OutputCache`) + CLI
  `seahorse benchmark run/list/adapters`. OQ-16-11 (bridge test
  `WriteResult.fact_id == IndexRow.fact_id`) and OQ-16-12 (manifest
  `embedding_batch_config=batch_size=1_forced` + `knn_completeness=1.0`)
  closed. Delegation purity: the skeleton imports only from
  `seahorse.facade`; `datasets`/`tiktoken`/`litellm` are lazy (the `benchmark`
  extra). 1810 tests / coverage 96% / ruff+mypy clean.

## [0.3.0] - 2026-08-07

Sprint A — F1 recency seam (default-OFF), OQ3 summary enabler, and the
claude-mem importer (#15).

### Added

- **F1 recency as a ranking signal (seam default-OFF, ADR-10)** — a small,
  localized post-RRF step in `seahorse.retrieval.recall`:
  `apply_recency_boost` folds a bounded exponential decay
  (`score' = score · (1 + γ·exp(-ln2·age_days/half_life))`, factor in `[1, 1+γ]`)
  INTO `FusedCandidate.score` (never an external reorder). Gated on `pit is
  None` (PIT queries reproduce state as-of-`t` with pure RRF); default-OFF
  (`recency=None`) preserves the bit-comparable fingerprint. `created_at` is
  batch-read via `index_repo.get_rows` (one `IN` query, no N+1). Pins
  `RECENCY_GAMMA`/`RECENCY_HALF_LIFE_DAYS` in `retrieval/constants.py`;
  `HybridRetriever` propagates `RecencyConfig | None`.
- **OQ3 enabler — `remember` accepts `summary`** (f5-09 §6.2): `summary` is an
  additive editorial field on `RememberPayload` (facade + CLI `--summary` + MCP
  wire). When absent, the write path derives a deterministic zero-LLM fallback
  (first sentence of the body, skipping the H1, truncated to
  `SUMMARY_MAX_CHARS=200`) — covers 100% of episodes including the skip path.
  `engine.remember` persists it; the frontmatter round-trip preserves it.
- **claude-mem importer (#15)** — the migration/coexistence bridge
  (obsiforge §15.4): a pure `import_record` (f5-15 pattern) mapping claude-mem
  observations to F3.1 episodes + a loss report, wrapped by an `ImportRunner`
  (dry-run/commit, manifest `seahorse.importer.manifest/1.0`, idempotency via
  deterministic UUIDv5, collisions via `WriteResult.collisions_detected` —
  never raised). `source_type=importer` forces the skip path (existing
  `decide_path` guard); claude-mem is NEVER a runtime dependency. New CLI
  command `seahorse import [--source] [--mode dry-run|commit] [--project]`.

## [0.2.0] - 2026-08-06

MVP-1 sealed. Hybrid semantic retrieval (sqlite-vec kNN + FTS5 BM25 fused by
Reciprocal Rank Fusion) with PIT routing, the Embedder `#7` (FastEmbed ONNX,
mE5-small) wired, a real multi-LLM extraction path (`#4`/`#5`) with a local-first
CI gate, and an honest G2 degrade when vectors/embedder are unavailable.

### Added

- Migration 010: the `vec0` virtual table (`vec_episodes`, float[384]) + the
  FTS5 external-content pair (`episode_content` / `episode_fts`, `unicode61
  remove_diacritics 2`). `schema_version = 10`.
- `SqliteVectorIndexRepository` (kNN, vigent/fact_id/cognitive pushdown, PIT
  `state_at`/`known_at` via the shared `_pit_predicate`) and
  `SqliteFullTextIndexRepository` (BM25, `exp(-bm25)` scoring, PIT, subject
  filter) — real backends over the migration-010 tables.
- `seahorse/embeddings/`: `ModelIdentity` + async `Embedder` Protocol + L2
  normalization; the FastEmbed ONNX backend (mE5-small fp32-O4 bundle, OQ-7-12);
  a sync `QueryEmbedder` adapter (async→sync bridge); a query cache (SQLite
  `embeddings_cache` + LRU); and a `RetrievalIndexer` that populates vec0/FTS
  from the write path and `seahorse index rebuild` (best-effort, ADR-10).
- `HybridRetriever`: the facade recall regime over `seahorse.retrieval.recall`
  (RRF fusion, PIT routing), with an honest degrade to the vigente listing when
  vectors/embedder are unavailable.
- PIT recall support in the MVP-1 regime (the facade guard is now conditioned on
  the retriever's `supports_pit` capability).
- New optional `embeddings` extra (`fastembed`, `onnxruntime`) — `uv sync
  --extra dev` stays G2/offline (no model download).
- `seahorse/llm/`: errors taxonomy (retry/content/permanent), providers registry
  (ollama/gemini/groq/openrouter/openai/anthropic/deepseek/vllm — local-first +
  the free-tier palanca), extraction role routing, operative cost cap (local and
  free-tier models price at $0; paid rows verified), plain-prompt parser + Pydantic
  validator (`extra="forbid"` → repair loop, `<content>` injection delimiters),
  retry/fallback chain (backoff + jitter), and the `LiteLLMBackend` (optional `llm`
  extra, sync C8.7).
- `run_llm_path` (write path) with a strict `EpisodeFrontmatter` (subject
  REQUIRED); `engine.remember` gains an additive `subject` override (M4-C.3);
  `build_facade` gains the `llm_client` slot.
- CLI onboarding: `seahorse init --llm` no-TUI provider wizard (detects Ollama /
  free-tier keys; factory default local-first `ollama/qwen3:1.7b`, 0.6b low-end);
  `[llm]` block in `seahorse.toml`; `status` reports the LLM regime; `seahorse
  doctor` (config + key NAMES + live provider probe).
- CI gate `ci-llm-gate.yml` (f5-04 §2.2): the real extraction path is run against
  the WEAKEST model of the family (`ollama/qwen3:0.6b`, pinned Ollama image + tag,
  CPU) so the Pydantic validator + retry + repair must carry the load — proves the
  path does not silently depend on native structured outputs (ADR-05) or on a
  strong model. Gated tests in `tests/llm/test_gate_ollama.py`
  (`SEAHORSE_RUN_LLM_TESTS=1`, `pytest -m llm_gate`); the main `ci.yml` is
  untouched (still no litellm).
- **Frontmatter round-trip: `extraction_mode=consolidated` un-reserved**
  (obsiforge §5.4 — MINOR bump). The value is now schema-valid and
  round-trippable (wire + facade Literal + frontmatter, case-C idempotent): a
  batch-distilled note with `extraction_mode=consolidated` parses, round-trips
  byte-identically, and classifies as case C (untouched on re-run). The wire
  enum is single-sourced from the facade `ExtractionMode` Literal (parity
  #13/#14). `llm_partial` stays fully reserved.

### Fixed

- The extraction prompt (`build_extract_prompt`) now states two rules verbatim
  for weak models (gate finding 2, 2026-08-05): `subject` is a short topic
  phrase — never a bare date; `valid_at` must be a timezone-aware ISO-8601
  datetime, so a bare date is omitted rather than emitted (I2 rejects naive
  datetimes). A weak model (`qwen3:0.6b`) previously used a bare date as both
  `valid_at` and `subject`, wasting repair calls; first-call validity on
  date-bearing content went 0/3 → 3/6 in the smoke.

### Known Limitations

- The mE5-small bundle is fp32-O4 (~235MB): OQ-7-12 verified no int8/fp16
  artifact is portable to Apple Silicon; a portable int8 bundle is a measured
  follow-up (Optimum quantization + per-platform benchmark).
- Without the `embeddings` extra (or with no vectors populated), `recall` is the
  honest G2 vigente listing (no ranking) and PIT recall is refused.
- The BFS-as-INDEX retrieval axis (graph expansion into the fusion) is mediano;
  the supersedes chain is already fused.
- **Batch distillation is NOT built yet** (ADR-10): `consolidated` is a valid,
  round-trippable schema value, but the engine does not produce it — the
  `distill_episodes` primitive is Sprint B (post-F7) and writes via
  `engine.remember` directly, bypassing the single-episode write path (which
  refuses `consolidated` loud).
- Reserved CLI commands (`expire`, `revalidate`, `vigentes`, `activos-ahora`,
  `index verify`) still exit `75`.

## [0.1.0] - 2026-07-29

First tagged release. MVP-0 is functionally complete and runnable end-to-end from
a clean install: the memory engine records, recalls, improves, and forgets
episodes, and serves an agent over stdio MCP.

### Added

- Bi-temporal, append-only episode store on stdlib `sqlite3` (single-file,
  zero-infra). Auto-migrating schema (`schema_version = 9`).
- The 7 memory-native primitives on both the CLI and stdio MCP
  (`io.seahorse.memory/v1`, protocol pinned `2025-11-25`):
  `remember`, `recall`, `recall_timeline`, `recall_full`, `improve`, `forget`,
  `build_pit`.
- Progressive disclosure across three retrieval levels — INDEX (vigente listing),
  TIMELINE (supersedes chain), FULL (hydrated episode) — plus point-in-time
  projection via `build_pit`.
- Supersession (`improve`) and soft-delete (`forget`), append-only; full history
  is preserved and reproducible at any past point in time.
- Two console scripts: `seahorse` (humans/scripts) and `seahorse-mcp` (agents);
  the `seahorse mcp` subcommand delegates to the same stdio server as
  `seahorse-mcp`. `serverInfo.version` is single-sourced from package metadata.
- Frontmatter import/export for the Obsidian vault layer (markdown as the
  portable on-disk contract, ADR-02).
- Honest exit codes with a structured `{"error": {...}}` envelope on stderr
  (`seahorse_code` / `cli_code` / `exception_class`) so agents and scripts branch
  deterministically.
- Systematic functional review committed as regression tests: subprocess CLI
  smoke (full MVP-0 matrix), real-stdio MCP smoke, and a gated install smoke
  proving the "clone, install, run" promise.

### Known Limitations

- `recall` returns the **vigente listing** clamped to `top_k`; the query is
  validated non-empty but does **not** filter or rank in v0.1.0. This is
  deliberate and documented, not a gap.
- No embeddings, vector search, or FTS5 retrieval yet — that is MVP-1
  materialization (sqlite-vec, the Embedder `#7`, hybrid retrieval wiring).
- No LLM extraction yet — the skip-path is first-class so an agent records at
  near-zero cost today; LLM extraction is deferred.
- Reserved CLI commands (`expire`, `revalidate`, `vigentes`, `activos-ahora`,
  `index verify`) are wired to return exit `75` (`CLI_NOT_IN_MVP_0`) with a
  reason, rather than silently no-op'ing.
- The FastAPI / SQLAlchemy / LiteLLM / multilingual-e5 / ONNX stack from the
  long-term design is **not** in v0.1.0; it lands in MVP-1 and the multi-agent
  rung (Postgres + pgvector).

### References

- Design: Seahorse Obsidian vault, Fase 5 detailed-design docs `f5-01`–`f5-16`.
- Roadmap: see the vault `Roadmap/` for the MVP-0 → MVP-1 path.