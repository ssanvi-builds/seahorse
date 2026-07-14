# Seahorse

Open standard for persistent, self-evolving LLM agent memory. Monetized open-core:
an Apache-2.0 reference standard plus a proprietary SaaS and an enterprise self-host
BSL track. The acquisition-by-a-lab path is explicitly **not** a goal (ADR-011).

> Status: **pre-MVP**. Detailed design (Fase 5, 16/16 components) is complete; the
> reference implementation (Fase 6) starts with the Persistence Layer (#6) and the
> Bi-temporal Engine (#2). There is no runnable engine yet.

## What it is

A bi-temporal, append-only memory engine for LLM agents that turns episodic notes
into a queryable, conflict-aware, point-in-time-reproducible knowledge base. It is
built so an agent can write thousands of episodes at near-zero cost (skip extraction
is a first-class citizen, ADR-09) and reserve LLM extraction for the few episodes
that justify it.

## Architecture (three memory layers)

| Layer | What | Where |
|------|------|-------|
| 1. claude-mem | Session observations ("how did we fix X") | local worker |
| 2. Obsidian vault | Project knowledge, decisions, preferences | human-readable markdown |
| 3. Native pointer | Pointer only, never duplicated knowledge | per-session |

The engine itself is storage-agnostic across two rungs:
- **Rung 1 (MVP, zero-infra):** SQLite + sqlite-vec + FTS5 in a single file.
- **Rung 2 (multi-agent):** Postgres + pgvector + recursive-CTE flat SQL.

## Stack

- Python (FastAPI + SQLAlchemy for writes, raw asyncpg for hot reads at Rung 2).
- SQLite WAL single-writer/multi-reader at Rung 1 (ADR-04).
- LiteLLM multi-backend with Ollama day 1 (ADR-05). No hard structured-outputs dependency.
- multilingual-e5-small (384-d) embeddings, INT8 ONNX bundle default.
- MCP as the only agent surface; a portable on-disk format is the real contract (ADR-02).
- No LLM in the query path (ADR-10): hybrid retrieval is kNN + BM25 + chain/BFS fused
  by Reciprocal Rank Fusion in pure Python.

## Design & decisions

Authoritative design lives in the Seahorse Obsidian vault (Fase 5 detailed-design
docs `f5-01` through `f5-16`, plus the F6 open-questions & sign-off register). The
8 blocking contract decisions are signed; ranks 9–15 close inline during F6.

## License

Apache-2.0. See [LICENSE](LICENSE).