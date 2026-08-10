# Changelog

All notable changes to Seahorse are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Sprint B (todo-en-uno, 2026-08-10)

- **observe (#17)** — the capture layer (`seahorse/observe/`, stdlib-only, zero
  harness imports in the core): `protocol.py` (tolerant envelope, caps ≤256) ·
  `redact.py` (deterministic structural JSON walk, bearer/API keys/PEM/
  userinfo/prefixes, pure) · `threshold.py` (skip_tools WebSearch/WebFetch vs
  drop_tools Read/Bash) · `batcher.py` (deterministic turn render, H1
  `{first line} [session_tag:prompt_number]`, byte truncation never splitting a
  codepoint) · `queue.py` (own SQLite, ConnectionManager, single-writer WAL+ack,
  dedup `(session_id, prompt_number, event_fingerprint)` INSERT OR IGNORE,
  **prompt_number persisted** §15.2 redesign 2) · `worker.py` (drains by
  session — por_sesion, skip-first ADR-09, OQ3 summary skipping the H1) ·
  `endpoint.py` (unix socket 0600 + auth token §15.2 redesign 10, redacts
  before enqueue, drop_tools never persisted) · `runner.py` (endpoint thread +
  worker loop) · `adapters/claude_code.py` (4 hooks: SessionStart /
  UserPromptSubmit / PostToolUse / Stop — the only harness binding) · CLI
  `seahorse observe start|stop|status|run|event`.
- **context (§6)** — `MemoryFacade.context()` (four INDEX-level blocks: recent
  G2 sort, vigente, last session grouped by `provenance.session_id` — INDEX
  list not abstractive summary, header + counter + pointer) +
  `seahorse/context/assembler.py` (pure render) + CLI `seahorse context`
  (degrades to "no context" without a DB).
- **consolidate (§5)** — `seahorse/distill/`: `cluster.py` (clustering key
  distinct from the stored subject §15.2 redesign 1, N≥3) · `distill.py`
  (`distill_episodes` → `engine.remember(cognitive_type=semantic,
  extraction_mode=consolidated, supersedes=representative,
  supersedes_reason=merge, synthetic consolidate-* session)`, sources stay
  vigente) · `consolidate.py` (idempotent §5.5, session-end signal OFF §15.2
  redesign 5) + `engine.remember` additive `supersedes`/`supersedes_reason` +
  CLI `seahorse consolidate`.
- **setup (§4.7)** — `seahorse setup` (writes `[observe]` with a generated
  auth token + merges the Claude Code hooks into `~/.claude/settings.json`
  coexisting with claude-mem §15.3-4) + `--uninstall` (`observe event` marker).
- `[observe]` section in `seahorse.toml` (opt-in until `seahorse setup`).

### Added

- **F7 experiment (b) rerank — cross-encoder seam + harness + run (f7 §5b)**.
  - **ADR-10 enmienda (vault)**: "no LLM generativo en el query path; un
    cross-encoder dedicado, pinneado por identidad de modelo y con presupuesto
    de latencia separado, está permitido como opt-in." Distinción estructural:
    generativo autoregresivo (prohibido) vs discriminativo rank-only on-device
    (opt-in). El gate de benchmark y el tipo de modelo (bge-reranker-v2-m3,
    MIT) van ANTES de tocar el ADR (cerebras-f §4.4).
  - **Seam `QueryReranker`** (`contracts/rerank.py`, patrón frontier
    `QueryEmbedder`): `rerank(query, docs) -> Sequence[float]`. Owned by #11.
  - **Stage-3 en `recall()`** (`retrieval/engine.py` + `retrieval/rerank.py`):
    fusiona con `k_rerank=20`, hidrata summary/subject vía
    `index_repo.get_rows` (NO body_md), puntúa pares, reordena, truncate a `k`.
    El score del cross-encoder REEMPLAZA el RRF score (`score_source:
    "rrf_rerank"`). Degrade honesto (ADR-10): sin `index_repo` o fallo del
    reranker → orden RRF truncado a `k`. Constantes `RERANK_OVERFETCH_K=20` /
    `INDEX_RERANK_P95_MS=500`.
  - **Backend cross-encoder** (`embeddings/rerank_backend.py`): `FastEmbedReranker`
    sobre fastembed `TextCrossEncoder` (0.8.0, sin dependencia nueva). Bundle
    `hooman650/bge-reranker-v2-m3-onnx-o4` (MIT, multilingüe, ~1.1GB) —
    validado en arm64: 20 pares ≈ 204ms (dentro del budget 500ms). El jina
    default es cc-by-nc-4.0 (incompatible Apache-2.0, ADR-011).
  - **`build_facade(..., reranker)`** → `HybridRetriever` (single-point swap,
    default None = RRF puro ADR-10). Query-time puro: cambiar el modelo nunca
    exige reindex.
  - **Harness F7**: variants `mvp1_rrf` vs `rrf_rerank`, `decide_rerank`
    (ndcg@10 ≥ 2pp Y p95 ≤ 500ms; `invalid_regime` en `fallback_g2`),
    `LatencyP95RerankMetric` (lee `latency_ms["index_rerank"]`), `rerank_model`
    pinneado en la fingerprint (run_id distinto del baseline), `HashReranker`
    sintético (token-overlap, puntuación normalizada), CLI `--rerank-enable`.
  - **Run LMEB-S (submuestreado, 2026-08-08)**: **keep_rrf — F2 NO implementado**.
    El cross-encoder degrada ambas métricas: ndcg@10 −2.8pp (0.110 → 0.082),
    recall@10 −2.4pp (0.130 → 0.106), y p95_index_rerank_ms 1244ms > 500ms
    (2.5× el budget). El seam `QueryReranker` + stage-3 + backend quedan como
    opt-in inactivo (default None), listos para re-evaluar.
- **F7 experiment (d) batch-por-turno — harness + authoritative run (f7 §5d)**.
  - **Importer turn-structure preservation** (`seahorse/importer/claude_mem.py`):
    the vendor `memory_session_id` and `prompt_number` now survive in provenance
    as `x-claude-mem-session-id` / `x-claude-mem-prompt-number` (f5-15 §6.8
    preservation convention). The run-scoped `session_id` contract (f5-15 §3.3)
    is unchanged; the loss report documents the preservation instead of the
    loss. The batch-por-turno experiment needs the turn structure, which the
    importer previously dropped (measured against the real DB — ADR-10).
  - **Batch experiment harness** `seahorse/benchmark/experiments/batch.py` — a
    standalone measurement (no `EvaluationRunner`/`BenchmarkDataset`: (d) is a
    retrieval-clustering measurement, not a QA benchmark): corpus builders
    (real claude-mem via importer #15 + synthetic with `HashEmbedder` for CI),
    `compute_turn_clusters` (groups by `x-claude-mem-session-id` +
    `x-claude-mem-prompt-number`), leave-one-out cluster recall@k (batch-por-
    turno) + individual recall@k (por-sesión), `decide_batch` (threshold 0.5,
    `invalid_regime` on `fallback_g2` — ADR-10). Registered in
    `variants.py`/`runner.py`/CLI (`seahorse benchmark experiment batch
    --corpus claude-mem|synthetic`).
  - **Authoritative run (2026-08-08, real claude-mem corpus)**: 281 obs → 236
    episodes (45 subject collisions excluded), 27 turns ≥2 obs, 215 leave-one-
    out queries, real fastembed backend (hybrid, no `fallback_g2`). **Decision
    `por_sesion`**: cluster recall@10 = 0.336 < 0.5 threshold (individual
    recall@10 = 1.000; robust across k=5/10/20 → 0.224/0.336/0.336). The turn
    is NOT a recoverable unit — Sprint B degrades to per-session batching.
    Large turns (diverse observations) are the drag; small focused turns are
    recoverable. 1896 tests / ruff+mypy clean.
- **F7 experiments (a) recency + (c) embed — harness + enablers (f7 §5)**.
  - **Enabler (a)** `build_facade(..., recency: RecencyConfig | None)` →
    `HybridRetriever` (composition root, single-point swap); default `None`
    keeps the pure-RRF bit-comparable fingerprint (ADR-10). Benchmark CLI gains
    `--recency-gamma` / `--recency-half-life` (pair-or-error); the recency
    config is baked into `BenchmarkConfig.config_hash` so the run_id differs
    from the baseline.
  - **Enabler (c)** `RetrievalIndexer(embed_mode="body" | "body+summary")` —
    `body+summary` embeds `summary\n\nbody` (summary leads), honest body-only
    fallback when no summary, `content_hash` over the EFFECTIVE text (reindex
    under a new mode re-embeds via cache miss). `EMBED_MODES` single-sourced in
    `embeddings/types.py` (numpy-light, import-laziness preserved). Benchmark
    CLI + `seahorse index rebuild` gain `--embed-mode`.
  - **Correctness fixes the experiments exposed**: `improve` now indexes the
    successor via a `MemoryFacade.on_episode_improved` hook wired to the
    write-path indexer in the hybrid regime (G2 no-op) — `knowledge_update_accuracy`
    (f5-16 §4.6) becomes measurable in hybrid; `SeahorseSUT` evaluates
    temporal-reasoning questions with `pit=state_at(question_date)` (f5-16
    §3.4) with an honest G2 degrade (active-now, no crash).
  - **Experiment harness** `seahorse/benchmark/experiments/` — variants ((a)
    baseline `mvp1_rrf` + 9-combo sweep γ∈{0.25,0.5,1.0} × half_life∈{7,30,90}d;
    (c) `body` vs `body+summary`), pure decision logic (f7 §5 thresholds:
    recency must not degrade global ndcg@10 >1pp AND improve recall@10 on
    temporal-reasoning/knowledge-update ≥1pp; embed recall@10 ≥1pp to flip F3;
    `invalid_regime` when the baseline ran `fallback_g2` — ADR-10 honesty),
    deterministic synthetic corpus + `HashEmbedder` (content-hash passages keep
    the vec0 kNN path real without a model download — mechanical CI
    verification), and `run_experiment` (wires the `AdvancingClock`, base =
    earliest session date, delta = 1 day → `created_at` spans the haystack;
    runs each variant via `EvaluationRunner`, writes per-variant manifest
    artifacts, applies the decision) + CLI `seahorse benchmark experiment
    --experiment recency|embed --corpus synthetic|lmeb-s [--temporal]`.
  - **1859 tests / ruff+mypy clean.**
- **F7 LMEB-S run (a)(c) — authoritative F1/F3 decision (2026-08-07)**.
  - **Warm-DB shared ingest**: variants that embed the same text share ONE
    corpus ingest (recency = 10 variants over identical embeddings; fresh-DB
    would re-embed ~49M tokens × 10 ≈ 28h, warm-DB ≈ 2 ingests). The template
    DB is copied per variant (fast) and the SUT re-attaches the bridge
    (`skip_ingest`); variant clocks seed from the template's post-ingest
    position so the recency boost reads the same `now` vs `created_at` spread
    (bit-identical metrics vs fresh-DB, verified by test).
  - **Retrieval-only pass**: `StubReaderLLM` + `--retrieval-only` — the F1/F3
    decision metrics (recall@10/ndcg@10) never consume the reader's answer
    (f5-16 §4.4 honest floor), so a retrieval-only run gives identical decision
    numbers with zero Ollama cost.
  - **Correctness fixes the real run surfaced**: LMEB loader broken under
    pyarrow 25 (`OverflowError` int32 on the mixed-type `/answer` column) →
    stdlib-json prematerialization via `hf_hub_download` (no
    `trust_remote_code`); real LMEB row shape (parallel session arrays) +
    `parse_date` for `YYYY/MM/DD (Weekday) HH:MM`; skip-path title derivation
    for conversational turns + empty-turn filter; clock delta derived from the
    haystack date spread (fixed 1-day-per-write made `created_at` span 547
    years); `SeahorseSUT.pit_queries=False` + non-temporal ingestion so the
    recency boost (gate `pit is None`) actually fires; nDCG dedup for repeated
    golden sessions (was >1.0); fastembed idempotent registration (warm-DB
    variant facades silently degraded to G2); `HybridRetriever` top_k cap
    parity with G2.
  - **Decisions (subsampled LMEB-S, 100/500 questions balanced — full corpus
    ingests ~2.2h/template + FTS5 hang; honest label `subsampled_lmeb_s`)**:
    **F1 `keep_off`** — the recency boost is applied (ndcg@10 varies
    0.390-0.398) but does not change the top-10 set (recall@10 identical 0.467;
    slices tr=0.372/ku=0.717); F1 stays default-off. **F3 `flip_f3`** — LMEB
    episodes DO have summaries (derived by `deterministic_extract` first
    sentence); `body+summary` embeds `summary\n\nbody` → recall@10 +2.7%
    (0.467→0.494) ≥1% threshold; flip F3 vectorial via `seahorse index rebuild
    --embed-mode body+summary`.
  - **1876 tests / ruff+mypy clean (144 src files).**
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

### Changed

- **F3 vectorial flip applied (f7-experiment-embed §decide, 2026-08-08)** — the
  `embed_mode` default flips from `body` to `body+summary` (summary leads the
  passage vector) in every product default: `build_facade` (composition root),
  the benchmark CLI + `BenchmarkConfig`, `SeahorseSUT`, the `seahorse index
  rebuild` backfill, and `RetrievalIndexer` itself. Reindexing under the new
  default re-embeds honestly — `content_hash` is computed over the EFFECTIVE
  text (`summary\n\nbody`), so the body-only vectors are a genuine cache miss
  (f7 §5c). The F3 experiment A/B remains explicit and intact
  (`embed_mode='body'` baseline in `benchmark/experiments/variants.py`); the
  flip is a product default, not a harness change. Verified: reindex of a test
  vault under the new default populates vec0/FTS and hybrid recall returns
  non-zero RRF scores (no `fallback_g2`). Decision source: LMEB-S subsample
  (100/500 questions), recall@10 +2.7% (0.467→0.494) ≥1% threshold. 1877 tests
  / ruff+mypy clean (144 src files).

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