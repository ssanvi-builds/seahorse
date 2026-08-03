# Changelog

All notable changes to Seahorse are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-03

MVP-1 materialization: hybrid semantic retrieval lands (sqlite-vec kNN + FTS5
BM25 fused by Reciprocal Rank Fusion), the Embedder `#7` (FastEmbed ONNX,
mE5-small) is wired, and `recall` ranks by relevance with an honest G2 degrade.

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

### Known Limitations

- The mE5-small bundle is fp32-O4 (~235MB): OQ-7-12 verified no int8/fp16
  artifact is portable to Apple Silicon; a portable int8 bundle is a measured
  follow-up (Optimum quantization + per-platform benchmark).
- Without the `embeddings` extra (or with no vectors populated), `recall` is the
  honest G2 vigente listing (no ranking) and PIT recall is refused.
- The BFS-as-INDEX retrieval axis (graph expansion into the fusion) is mediano;
  the supersedes chain is already fused.
- No LLM extraction yet — the skip-path is first-class (ADR-09).
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