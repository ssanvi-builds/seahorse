# Seahorse

Open standard for persistent, self-evolving LLM agent memory. Monetized open-core:
an Apache-2.0 reference standard plus a proprietary SaaS and an enterprise self-host
BSL track. The acquisition-by-a-lab path is explicitly **not** a goal (ADR-011).

> Status: **MVP-0 (v0.1.0) — runnable**. The memory engine works end-to-end from a
> clean install: write episodes, recall them, improve and forget them, and serve an
> agent over stdio MCP. Retrieval in v0.1.0 is an honest vigente listing (no
> embeddings/vector/FTS, no ranking) — see [What works in v0.1.0](#what-works-in-v010)
> and [Reserved for MVP-1](#reserved-for-mvp-1).

## What it is

A bi-temporal, append-only memory engine for LLM agents that turns episodic notes
into a queryable, conflict-aware, point-in-time-reproducible knowledge base. It is
built so an agent can write thousands of episodes at near-zero cost (skip extraction
is a first-class citizen, ADR-09) and reserve LLM extraction for the few episodes
that justify it.

Every episode is recorded with two time axes — when it became true (`valid_time`)
and when it was recorded (`recording_time`) — so the knowledge base is reproducible
at any past point in time. Supersession and soft-delete are append-only: an
`improve` invalidates the prior episode and supersedes it; a `forget` invalidates
without destroying history.

## Quickstart

```bash
# Install (editable or from a wheel):
uv tool install .
# …or, in a checkout:
uv sync --extra dev

# Create a vault and write your first episode:
seahorse init myvault
seahorse remember "Sergio lives in Madrid" --title home
seahorse recall "madrid"

# Improve and forget (append-only; history is preserved):
seahorse improve <ep_id> "Sergio lives in Barcelona" --reason correction
seahorse forget <ep_id> --reason done

# Serve an agent over stdio MCP (io.seahorse.memory/v1):
seahorse-mcp --vault myvault
# …equivalently:
seahorse mcp --vault myvault
```

The `seahorse` console script is for humans and shell scripts; `seahorse-mcp` is
for agents. The `seahorse mcp` subcommand invokes the same stdio server as
`seahorse-mcp`, so both agent entry points are equivalent.

## The agent surface — 7 memory-native primitives

Exposed over stdio MCP (`io.seahorse.memory/v1`, protocol pinned `2025-11-25`) and
mirrored on the CLI. These are memory primitives, not generic CRUD: an agent calls
`remember` / `recall` / `improve` / `forget` the way a human would talk about memory.

| Primitive | What it does |
|-----------|--------------|
| `remember` | Record an episode (body, source, optional title/subject). |
| `recall` | INDEX level — the current vigente listing, clamped to `top_k`. |
| `recall_timeline` | TIMELINE level — the supersedes chain around an anchor episode. |
| `recall_full` | FULL level — the hydrated episode with all provenance. |
| `improve` | Supersede an episode with a corrected one (append-only). |
| `forget` | Soft-delete an episode (append-only; history preserved). |
| `build_pit` | Build a point-in-time projection (all-None → current state). |

Three retrieval levels give **progressive disclosure**: a cheap listing first
(INDEX), the chain on demand (TIMELINE), and the full record only when needed
(FULL). This keeps the common path cheap.

## What works in v0.1.0

- Bi-temporal, append-only episode store on stdlib `sqlite3` (single-file, zero
  infra). Auto-migrating schema (currently at `schema_version = 9`).
- The 7 memory-native primitives, on both the CLI and stdio MCP.
- Progressive disclosure (INDEX / TIMELINE / FULL) and point-in-time projection.
- Supersession (`improve`) and soft-delete (`forget`) with full history preserved.
- Frontmatter import/export for the Obsidian vault layer (markdown as the
  human-readable, portable on-disk contract — ADR-02).
- Honest exit codes and a structured `{"error": {...}}` envelope on stderr, so
  agents and scripts can branch on `seahorse_code` / `cli_code` deterministically.
- `recall` is the **vigente listing** clamped to `top_k`: the query is validated
  non-empty but does **not** filter or rank in v0.1.0. This is deliberate and
  documented, not a gap.

## Reserved for MVP-1

The following CLI commands are wired but intentionally return exit `75`
(`CLI_NOT_IN_MVP_0`) with a reason, so the surface is honest about what is not
implemented yet rather than silently no-op'ing:

- `expire`, `revalidate`, `vigentes`, `activos-ahora`, `index verify`.

MVP-1 will add materialization-based retrieval — sqlite-vec vectors, FTS5 full-text,
the Embedder (`#7`), and wiring `recall` to the hybrid retrieval engine (kNN + BM25 +
chain/BFS fused by Reciprocal Rank Fusion). LLM extraction stays deferred in v0.1.0;
the skip-path is first-class so an agent can record at near-zero cost today.

## Architecture (three memory layers)

| Layer | What | Where |
|------|------|-------|
| 1. claude-mem | Session observations ("how did we fix X") | local worker |
| 2. Obsidian vault | Project knowledge, decisions, preferences | human-readable markdown |
| 3. Native pointer | Pointer only, never duplicated knowledge | per-session |

## Stack (v0.1.0)

- Python ≥ 3.11. stdlib `sqlite3` for storage (zero-infra single file).
- Pydantic v2 for the canonical `Episode` contract (core type system).
- Typer for the CLI surface (humans and scripts). Confined to `seahorse.cli`.
- stdio JSON-RPC 2.0 for the MCP agent surface (hand-rolled framing, stdlib-only
  `seahorse.mcp` package — `import seahorse.mcp` does not load Typer).
- `ruamel.yaml` + `python-frontmatter`, confined to the frontmatter adapter.

> The FastAPI / SQLAlchemy / LiteLLM / multilingual-e5 / ONNX stack from the
> long-term design is **not** in v0.1.0. Those land in MVP-1 (retrieval
> materialization, LLM extraction) and the multi-agent rung (Postgres + pgvector).
> The README states what ships now, not the target architecture.

## Design & decisions

Authoritative design lives in the Seahorse Obsidian vault (Fase 5 detailed-design
docs `f5-01` through `f5-16`, plus the F6 open-questions & sign-off register). The
8 blocking contract decisions are signed; ranks 9–15 close inline during F6. See
[CHANGELOG.md](CHANGELOG.md) for release history.

## License

Apache-2.0. See [LICENSE](LICENSE).