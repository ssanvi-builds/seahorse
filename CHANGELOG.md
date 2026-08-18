# Changelog

All notable changes to Seahorse are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Distillation supersession (`seahorse consolidate --supersede`)** — a cluster
  whose key already has a consolidated note gains NEW valid episodes → the note
  is UPDATED via `improve` (invalidate + atomic append) instead of duplicating.
  Editorial authority: a note whose vault `.md` was edited after its creation is
  human-touched and never superseded (the human prevails). Opt-in via the
  `[distill]` config section (`supersede = true`) or the CLI flag; the
  idempotent skip remains the default.
- **BM25 OR-of-terms query** — the FTS5 query is now built from the question's
  tokens (OR-of-terms) instead of phrase-quoting the whole query, so a
  natural-language question matches episodes containing any of its terms (the
  hybrid RRF was effectively kNN-only before).
- **Distillation indexes the consolidated note** — `facade.distill` now fires
  the write-path index hook, so the consolidated knowledge note is recoverable
  by hybrid recall (it was invisible to vec0/FTS before).
- **`[observe].drop_tools` applied at enqueue** — a tool added to the configured
  drop set never reaches the observer queue (previously it was only dropped at
  drain, so its redacted content persisted on disk).

## [0.8.0] - 2026-08-17

### Added

- **LLM synthesis for distillation (`seahorse consolidate --synthesis llm`)** —
  the off-path F7+ block: 1 LLM call per recurrent cluster (N episodes → 1
  fact), reusing the extractor seam (schema hint + repair + honest degrade).
  A failed synthesis degrades to the deterministic fallback with a durable
  `degraded_from="llm"` marker (ADR-10). Opt-in via the `[distill]` config
  section (`synthesis = "llm"`) or the CLI flag; the deterministic distillation
  remains the default.
- **`LLMClient.extract` `prompt_builder` seam** — an optional custom prompt
  builder lets callers reuse the full extraction pipeline (schema validation,
  repair loop, fallback chain, cost cap) with a different prompt. Additive and
  non-breaking.

## [0.7.0] - 2026-08-16

### Added

- **MCP `skill_list` / `skill_search` tools** — the MCP server now exposes the
  two Discovery-level skill tools (14 tools total), closing the parity gap with
  the CLI (which had `skill add|list|search|show` but no MCP listing/search).
- **CLI `freshness-view` / `audit-log` / `follow-supersedes-chain`** — the three
  read-only facade tools are now available on the terminal (parity with the MCP
  server, which already had them).
- **Timeline axes `created_at` / `valid_at`** — `recall-timeline` can now range
  over the transaction-time (`created_at`) and valid-time (`valid_at`) axes
  around an anchor's timestamp (±7 days), in addition to the supersedes chain
  and fact-id scope.
- **`--verbose` / `-v` flag** — per-operation timing on stderr
  (`[verbose] <label> took <X>ms`) for the memory primitives and skill commands.
- **BFS node budget** — the graph traversal is bounded to 1000 visited nodes,
  keeping exploration fast on large graphs (deterministic truncation).

## [0.6.0] - 2026-08-16

### Changed

- **Distribution renamed to `seahorse-memory`** — the PyPI name `seahorse` is
  taken by an unrelated project, so the distribution is now published as
  `seahorse-memory` (`pip install seahorse-memory`). The import package, the
  `seahorse` / `seahorse-mcp` console scripts, and the repo name are unchanged.
- **README rewritten for onboarding** — the README now leads with the problem
  and a Claude Code use case, adds a comparison with other memory tools, a
  curated benchmark section, and an FAQ, and moves the release-status block to
  the end. Internal jargon (`CLI_NOT_IN_MVP_0`, reserved Spanish command names)
  was removed from the public docs.
- **Benchmark published** — `docs/benchmark.md` documents the LMEB-S harness,
  the current numbers, their caveats, and how to reproduce them.
- **`scripts/demo.sh`** — a presentational walkthrough (init → remember →
  recall → improve → forget → import → observe status) for demos and recordings.

### Added

- **`seahorse frontmatter migrate`** — CLI command to migrate a legacy Obsidian
  vault (notes with no frontmatter, or legacy `tags`/`created` fields) to the
  canonical episode format. `--dry-run` classifies every note and writes nothing
  (always exit 0); `--resume` skips notes unchanged since the last manifest;
  `--batch-size` sets the manifest checkpoint cadence. Works before `seahorse init`.
- **Exit `97` `CLI_MIGRATION_DEFERRED`** — when apply meets incompatible notes
  (case D), the manifest summary is printed first, then the command fails loudly
  so scripts can see the vault is not fully migrated.
- **`publish.yml` workflow** — release-on-tag build + publish to PyPI as
  `seahorse-memory`.

### Fixed

- **Actionable `FrontmatterInvalid` error** — the message now names the migration
  command (`seahorse frontmatter migrate`) for legacy Obsidian notes, instead of
  pointing at the schema DDL runner.

## [0.5.1] - 2026-08-12

### Added

- **`scripts/e2e-matrix.sh`** — fresh-user end-to-end testing across environment
  combinations (install method × extras × Obsidian × Ollama × online/offline ×
  vault state × concurrency). 8 priority combos, isolated sandbox per combo,
  PASS/FAIL report. `--ci-subset` runs the CI-safe combos (`core_min` +
  `uv_sync_dev`); `--list` shows all combos.
- **`scripts/stress-core.sh`** — core load-test: ingest 1000+ episodes, recall
  `--top-k 100` p95 ≤ 250ms, concurrent single-writer, reindex, idempotent
  import, improve/forget chain.
- **CI job `e2e-matrix`** — runs the CI-safe matrix subset on every push/PR.

### Fixed

- **`scripts/e2e-fresh-user.sh`** — the MCP `tools/list` assertion now checks the
  7 memory primitives as a superset instead of an exact count of 7 (the surface
  grew to 12 tools).
- **Actionable `enable_load_extension` error** — a Python build without
  `SQLITE_ENABLE_LOAD_EXTENSION` (e.g. a pyenv build) used to crash every DB
  command with a cryptic `AttributeError`; it now fails with a hint to install
  with a supporting Python (`uv tool install --python 3.13`).
- **Actionable `E_FRONTMATTER_INVALID`** — `index rebuild` on a legacy Obsidian
  note surfaced only raw pydantic validation errors; the message now says the
  note is not in the canonical format and names the required fields.

### Docs

- README: "7 memory-native primitives" → 7 primitives + 5 procedural/read-only
  tools (12 total); new Testing section (matrix + stress).

## [0.5.0] - 2026-08-10

### Added

- **Procedural skills** — `seahorse/procedural/`: `record_procedure`
  (deterministic creation, near-zero cost, canonical body
  `## Trigger/Steps/Validation/Rationale` validated before any write) ·
  `ProceduralShaper` (3-level progressive disclosure: Discovery = INDEX summary
  ≤280, Activation = TIMELINE, Execution = FULL) · trust gate (manual high /
  agent medium / import+distilled low; low-trust skills are delivered as
  citation/context, not instruction) · CLI `seahorse skill add|list|search|show`
  (add validates the canonical body, show applies the trust gate).
  `ProceduralError` maps to CLI exit 96 / MCP -32053.
- **Graph (BFS) timeline axis** — `materialize_timeline(axis=graph_bfs)`:
  a 1-2 hop, point-in-time-aware traversal of the supersedes graph,
  `HopsCapExceeded` for hops > 2, `cognitive_type=semantic` filter. Exposed via
  `recall-timeline --axis graph_bfs --hops` and the MCP `recall_timeline` `hops`.
- **Viewer TUI** — `seahorse view`: read-only interactive stdlib TUI
  (recent / search / timeline / skills), with an honest empty-vault degrade.
- **`[procedural]` config** — `seahorse.toml` section (min_trust + loadout
  defaults; opt-in, missing → module defaults).

## [0.4.0] - 2026-08-10

### Added

- **Session observer** — `seahorse/observe/` (stdlib-only): capture of agent
  sessions — a tolerant event envelope with size caps, deterministic redaction
  of sensitive values (bearer/API keys, PEM, userinfo, URL prefixes), tool
  thresholding (skip WebSearch/WebFetch, drop Read/Bash), deterministic turn
  batching (byte-truncation never splitting a codepoint), a dedicated SQLite
  queue (single-writer WAL + ack, dedup by session/turn/fingerprint), a worker
  that drains by session (skip-first, deterministic summary), a unix-socket
  endpoint (0600 + auth token, redacts before enqueue, dropped tools never
  persisted), and a Claude Code adapter (4 hooks: SessionStart / UserPromptSubmit
  / PostToolUse / Stop). CLI `seahorse observe start|stop|status|run|event`.
- **Context bootstrap** — `MemoryFacade.context()` (four INDEX-level blocks:
  recent, current-state, last session grouped by session — a list, not an
  abstractive summary) + `seahorse/context/assembler.py` (pure render) + CLI
  `seahorse context` (degrades to "no context" without a DB).
- **Consolidation** — `seahorse/distill/`: `cluster.py` (clustering key distinct
  from the stored subject, N≥3) · `distill.py` (writes a semantic episode with
  `cognitive_type=semantic` that supersedes the representative source; sources
  stay current) · `consolidate.py` (idempotent) + additive
  `supersedes`/`supersedes_reason` support on the write path + CLI `seahorse
  consolidate`.
- **Setup** — `seahorse setup` (writes `[observe]` with a generated auth token +
  merges the Claude Code hooks into `~/.claude/settings.json`, coexisting with
  claude-mem) + `--uninstall`.
- `[observe]` section in `seahorse.toml` (opt-in until `seahorse setup`).
- **Cross-encoder reranking stage (inactive opt-in)** — a `QueryReranker`
  contract, a stage-3 rerank step in `recall()`, and a FastEmbed cross-encoder
  backend. The default remains pure RRF fusion; an early evaluation on the
  benchmark corpus showed the cross-encoder degraded retrieval quality and
  exceeded the latency budget, so it stays disabled pending re-evaluation.
- **`--summary` on import with turn-structure preservation** — `seahorse import`
  preserves the source tool's session/turn identifiers in episode provenance
  (`x-claude-mem-session-id` / `x-claude-mem-prompt-number`), so the session
  structure survives the migration.
- **Embed-mode option** — `RetrievalIndexer(embed_mode="body" | "body+summary")`
  and `--embed-mode` on the benchmark CLI and `seahorse index rebuild`.
  `body+summary` embeds the summary followed by the body; reindexing under a new
  mode re-embeds honestly (the effective text changes).

### Fixed

- **`seahorse setup` crashed on a fresh user** (no `~/.claude/`): `merge_hooks`
  now creates the parent directory before writing `settings.json`. The observer
  is a Claude Code capture adapter, not a product binding — the rest of Seahorse
  is agent-agnostic.
- **Observer spawn**: `observe start` placed `--vault` after `observe run`, but
  it is a global option that must precede the subcommand — the observer died
  immediately with "No such option: --vault". Fixed in `observe/cli.py` + an
  order assertion in the test.
- **`seahorse doctor` prerequisite checks**: new `python` / `uv` / `obsidian` /
  `sqlite_vec` checks (the last detects a Python build whose `sqlite3` lacks
  `enable_load_extension`, which breaks sqlite-vec with a cryptic
  `AttributeError`).
- **README Prerequisites section** (Python ≥3.11 + sqlite load_extension, uv,
  Obsidian optional).
- **`scripts/e2e-fresh-user.sh`** — fresh-user E2E in an isolated sandbox
  (overridden HOME, temp vault; never touches the real `~/.claude` /
  `~/.claude-mem`). Validates install → init → core CLI → hybrid embeddings
  → LLM (honest degrade) → observer → import → MCP, with no-corruption
  post-flight checks.

### Changed

- **Embed mode default `body+summary`** — retrieval embeddings now lead with the
  summary (`summary\n\nbody`). This is the new default in the facade, the
  benchmark config, `seahorse index rebuild`, and the indexer; it measurably
  improved recall on the benchmark corpus. The `body`-only mode remains
  available for comparison.

## [0.3.0] - 2026-08-07

### Added

- **Recency as an opt-in ranking signal** — `apply_recency_boost`, a small
  bounded exponential-decay step applied after RRF fusion in `recall()`
  (`score' = score · (1 + γ·exp(-ln2·age_days/half_life))`, factor in
  `[1, 1+γ]`). Gated on point-in-time queries being off (PIT reproduces state
  as-of `t` with pure RRF); **default-off** (`recency=None`) preserves the
  bit-comparable fingerprint. `created_at` is batch-read (one `IN` query, no
  N+1). CLI flags `--recency-gamma` / `--recency-half-life`.
- **`remember --summary`** — `summary` is an optional editorial field on
  `RememberPayload` (facade + CLI `--summary` + MCP wire). When absent, the write
  path derives a deterministic zero-LLM fallback (first sentence of the body,
  skipping the H1, truncated to 200 chars) — covering 100% of episodes including
  the skip path. The write path persists it; the frontmatter round-trip
  preserves it.
- **claude-mem importer** — `seahorse import [--source] [--mode dry-run|commit]
  [--project]`: migrates claude-mem observations to episodes with a loss report,
  a manifest (`seahorse.importer.manifest/1.0`), idempotency via deterministic
  UUIDv5, and collision detection (never raised — reported). claude-mem is never
  a runtime dependency.

## [0.2.0] - 2026-08-06

Hybrid semantic retrieval (sqlite-vec kNN + FTS5 BM25 fused by Reciprocal Rank
Fusion) with point-in-time routing, a FastEmbed (ONNX, mE5-small) embedder, a
real multi-LLM extraction path with a local-first CI gate, and an honest degrade
to a current-state listing when vectors/embedder are unavailable.

### Added

- Migration 010: the `vec0` virtual table (`vec_episodes`, float[384]) + the
  FTS5 external-content pair (`episode_content` / `episode_fts`).
  `schema_version = 10`.
- `SqliteVectorIndexRepository` (kNN with current-state/fact_id/cognitive
  pushdown, point-in-time `state_at`/`known_at` predicates) and
  `SqliteFullTextIndexRepository` (BM25, `exp(-bm25)` scoring, PIT, subject
  filter) — real backends over the migration-010 tables.
- `seahorse/embeddings/`: `ModelIdentity` + async `Embedder` Protocol + L2
  normalization; the FastEmbed ONNX backend (mE5-small fp32-O4 bundle); a sync
  `QueryEmbedder` adapter; a query cache (SQLite + LRU); and a `RetrievalIndexer`
  that populates vec0/FTS from the write path and `seahorse index rebuild`
  (best-effort).
- `HybridRetriever`: the facade recall regime over `seahorse.retrieval.recall`
  (RRF fusion, point-in-time routing), with an honest degrade to the
  current-state listing when vectors/embedder are unavailable.
- Point-in-time recall support in the hybrid regime (the facade guard is now
  conditioned on the retriever's `supports_pit` capability).
- New optional `embeddings` extra (`fastembed`, `onnxruntime`) — the default
  install stays offline (no model download).
- `seahorse/llm/`: errors taxonomy (retry/content/permanent), providers registry
  (ollama/gemini/groq/openrouter/openai/anthropic/deepseek/vllm — local-first),
  extraction role routing, operative cost cap (local and free-tier models price
  at $0), plain-prompt parser + Pydantic validator (`extra="forbid"` → repair
  loop, `<content>` injection delimiters), retry/fallback chain (backoff +
  jitter), and the `LiteLLMBackend` (optional `llm` extra).
- `run_llm_path` (write path) with a strict episode frontmatter (subject
  required); the write path gains an additive `subject` override;
  `build_facade` gains the `llm_client` slot.
- CLI onboarding: `seahorse init --llm` no-TUI provider wizard (detects Ollama /
  free-tier keys; factory default local-first `ollama/qwen3:1.7b`, 0.6b low-end);
  `[llm]` block in `seahorse.toml`; `status` reports the LLM regime; `seahorse
  doctor` (config + key names + live provider probe).
- CI gate `ci-llm-gate.yml`: the real extraction path is run against the weakest
  model of the family (`ollama/qwen3:0.6b`, pinned Ollama image, CPU) so the
  validator + retry + repair must carry the load — proving the path does not
  silently depend on native structured outputs or on a strong model. Gated tests
  in `tests/llm/test_gate_ollama.py` (enabled via `SEAHORSE_RUN_LLM_TESTS=1`,
  `pytest -m llm_gate`); the main `ci.yml` is untouched (still no litellm).
- **Frontmatter round-trip: `extraction_mode=consolidated`** is now schema-valid
  and round-trippable (wire + facade + frontmatter, case-C idempotent): a
  batch-distilled note with `extraction_mode=consolidated` parses, round-trips
  byte-identically, and is left untouched on re-run. The wire enum is
  single-sourced from the facade `ExtractionMode` Literal. `llm_partial` stays
  fully reserved.

### Fixed

- The extraction prompt now states two rules verbatim for weak models: `subject`
  is a short topic phrase — never a bare date; `valid_at` must be a
  timezone-aware ISO-8601 datetime, so a bare date is omitted rather than
  emitted. A weak model previously used a bare date as both `valid_at` and
  `subject`, wasting repair calls.

### Known Limitations

- The mE5-small bundle is fp32-O4 (~235MB): no int8/fp16 artifact is portable to
  Apple Silicon; a portable int8 bundle is a measured follow-up (Optimum
  quantization + per-platform benchmark).
- Without the `embeddings` extra (or with no vectors populated), `recall` is the
  honest current-state listing (no ranking) and point-in-time recall is refused.
- The graph-expansion retrieval axis (BFS into the fusion) is a medium-term
  goal; the supersedes chain is already fused.
- **Batch distillation is NOT built yet**: `consolidated` is a valid,
  round-trippable schema value, but the engine does not produce it — the
  single-episode write path refuses it loudly.
- Reserved CLI commands (`expire`, `revalidate`, `vigentes`, `activos-ahora`,
  `index verify`) still exit `75`.

## [0.1.0] - 2026-07-29

First tagged release. The memory engine records, recalls, improves, and forgets
episodes end-to-end from a clean install, and serves an agent over stdio MCP.

### Added

- Bi-temporal, append-only episode store on stdlib `sqlite3` (single-file,
  zero-infra). Auto-migrating schema (`schema_version = 9`).
- The 7 memory-native primitives on both the CLI and stdio MCP
  (`io.seahorse.memory/v1`, protocol pinned `2025-11-25`):
  `remember`, `recall`, `recall_timeline`, `recall_full`, `improve`, `forget`,
  `build_pit`.
- Progressive disclosure across three retrieval levels — INDEX (current-state
  listing), TIMELINE (supersedes chain), FULL (hydrated episode) — plus
  point-in-time projection via `build_pit`.
- Supersession (`improve`) and soft-delete (`forget`), append-only; full history
  is preserved and reproducible at any past point in time.
- Two console scripts: `seahorse` (humans/scripts) and `seahorse-mcp` (agents);
  the `seahorse mcp` subcommand delegates to the same stdio server as
  `seahorse-mcp`. `serverInfo.version` is single-sourced from package metadata.
- Frontmatter import/export for the Obsidian vault layer (markdown as the
  portable on-disk contract).
- Honest exit codes with a structured `{"error": {...}}` envelope on stderr
  (`seahorse_code` / `cli_code` / `exception_class`) so agents and scripts branch
  deterministically.
- Systematic functional review committed as regression tests: subprocess CLI
  smoke (full first-release matrix), real-stdio MCP smoke, and a gated install
  smoke proving the "clone, install, run" promise.

### Known Limitations

- `recall` returns the **current-state listing** clamped to `top_k`; the query is
  validated non-empty but does **not** filter or rank in v0.1.0. This is
  deliberate and documented, not a gap.
- No embeddings, vector search, or FTS5 retrieval yet — that landed in v0.2.0.
- No LLM extraction yet — the skip-path is first-class so an agent records at
  near-zero cost today.
- Reserved CLI commands (`expire`, `revalidate`, `vigentes`, `activos-ahora`,
  `index verify`) return exit `75` with a reason, rather than silently
  no-op'ing.
- The FastAPI / SQLAlchemy / LiteLLM / multilingual-e5 / ONNX stack from the
  long-term design is **not** in v0.1.0; it lands in later releases and the
  multi-agent tier (Postgres + pgvector).
