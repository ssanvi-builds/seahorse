# Seahorse

Open standard for persistent, self-evolving LLM agent memory. Monetized open-core:
an Apache-2.0 reference standard plus a proprietary SaaS and an enterprise self-host
BSL track. The acquisition-by-a-lab path is explicitly **not** a goal (ADR-011).

> Status: **v0.3.0 — Sprint A (F1 recency + OQ3 summary + claude-mem importer)**.
> The memory engine works end-to-end from a clean install: write episodes, recall
> them with hybrid semantic retrieval (sqlite-vec kNN + FTS5 BM25 fused by RRF),
> extract with a real multi-LLM path (local-first, CI-gated), improve and forget
> them, and serve an agent over stdio MCP. Recall ranks by relevance when vectors
> are populated and the embedder is wired, and honestly degrades to a vigente
> listing otherwise. `remember` accepts an optional `--summary` (deterministic
> fallback when omitted); `seahorse import` migrates claude-mem observations to
> F3.1 episodes; and a recency ranking signal is available as a default-OFF seam.
> See [What works](#what-works) and [Reserved](#reserved).

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
# For hybrid semantic retrieval (FastEmbed ONNX, downloads mE5-small on first
# embed): uv sync --extra dev --extra embeddings
# For the multi-LLM extraction path (LiteLLM): uv sync --extra dev --extra llm

# Create a vault and write your first episode:
seahorse init myvault
seahorse remember "Sergio lives in Madrid" --title home
seahorse recall "madrid"

# Improve and forget (append-only; history is preserved):
seahorse improve <ep_id> "Sergio lives in Barcelona" --reason correction
seahorse forget <ep_id> --reason done

# The todo-en-uno (Sprint B): capture, context, consolidate.
# Install the observer (writes [observe] + merges the Claude Code hooks into
# ~/.claude/settings.json, coexisting with claude-mem):
seahorse setup
# Start the observer (unix socket + worker), then the next session is captured
# automatically (skip-first, redacted, deterministic summary):
seahorse observe start
seahorse observe status
# Bootstrap context by recency (the SessionStart hook injects this):
seahorse context
# Distill recurrent episodes into semantic knowledge notes (N≥3, idempotent):
seahorse consolidate
# Remove the observer:
seahorse setup --uninstall

# Serve an agent over stdio MCP (io.seahorse.memory/v1):
seahorse-mcp --vault myvault
# …equivalently:
seahorse mcp --vault myvault
```

The `seahorse` console script is for humans and shell scripts; `seahorse-mcp` is
for agents. The `seahorse mcp` subcommand invokes the same stdio server as
`seahorse-mcp`, so both agent entry points are equivalent.

> **First run**: the semantic-embedding model (mE5-small, ~235MB) downloads
> lazily on the first `remember`/`recall` — the CLI announces it so the first
> call doesn't look hung. `seahorse status` shows the active retrieval regime
> (`hybrid RRF (model cached)` vs `G2 listing — install seahorse[embeddings]
> for semantic recall`).

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

## What works

- Bi-temporal, append-only episode store on stdlib `sqlite3` + sqlite-vec (FTS5
  + vec0). Auto-migrating schema (currently at `schema_version = 10`).
- The 7 memory-native primitives, on both the CLI and stdio MCP.
- Progressive disclosure (INDEX / TIMELINE / FULL) and point-in-time projection.
- **Hybrid semantic retrieval (MVP-1)**: `recall` ranks by relevance — sqlite-vec
  kNN + FTS5 BM25 fused with Reciprocal Rank Fusion (`RRF_K=60`), with PIT
  routing (`state_at` / `known_at`) when a real embedder is wired. The write path
  and `seahorse index rebuild` populate vec0/FTS (best-effort — an embedder
  failure never fails the episode write, ADR-10).
- **Honest G2 degrade**: without the `embeddings` extra (or with no vectors
  populated), `recall` falls back to the vigente listing (score 0.0, no ranking)
  and PIT recall is refused — the motor keeps working without ranking.
- **LLM extraction (`#4`/`#5`)**: a real multi-LLM path (ollama / gemini / groq /
  openrouter / openai / anthropic / deepseek / vllm, local-first) with a strict
  schema validator + repair loop, retry/fallback chain, and an operative cost cap
  (local and free-tier models price at $0). `seahorse init --llm` bootstraps it;
  the skip-path stays the near-zero-cost default for the bulk of writes.
- **Local-first CI gate**: the real extraction path runs in CI against the
  weakest model of the family (`ollama/qwen3:0.6b`) so the validator + repair
  must carry the load — the path does not silently depend on native structured
  outputs (ADR-05) or a strong model.
- Supersession (`improve`) and soft-delete (`forget`) with full history preserved.
- Frontmatter import/export for the Obsidian vault layer (markdown as the
  human-readable, portable on-disk contract — ADR-02). `extraction_mode=
  consolidated` is a schema-valid, round-trippable batch-distillation marker
  (the value parses and round-trips today; the distillation engine is not built
  yet — see [Reserved](#reserved)).
- Honest exit codes and a structured `{"error": {...}}` envelope on stderr, so
  agents and scripts can branch on `seahorse_code` / `cli_code` deterministically.

## Reserved

The following CLI commands are wired but intentionally return exit `75`
(`CLI_NOT_IN_MVP_0`) with a reason, so the surface is honest about what is not
implemented yet rather than silently no-op'ing:

- `expire`, `revalidate`, `vigentes`, `activos-ahora`, `index verify`.

Batch distillation (`consolidated`) is **schema-valid but not built**: the wire
and frontmatter round-trip the value, but the single-episode write path refuses
it loud and the `distill_episodes` primitive lands in Sprint B (post-F7) — it
writes via `engine.remember` directly, not through `remember`. `llm_partial`
stays fully reserved.

The BFS-as-INDEX axis of retrieval (graph expansion into the fusion) is mediano —
`recall` fuses vector + BM25 + supersedes chain today.

## Architecture (three memory layers)

| Layer | What | Where |
|------|------|-------|
| 1. claude-mem | Session observations ("how did we fix X") | local worker |
| 2. Obsidian vault | Project knowledge, decisions, preferences | human-readable markdown |
| 3. Native pointer | Pointer only, never duplicated knowledge | per-session |

## Stack (v0.2.0)

- Python ≥ 3.11. stdlib `sqlite3` + sqlite-vec for storage (zero-infra single
  file; the `vec0` virtual table + FTS5 in migration 010).
- numpy for the embedding blob shape.
- Pydantic v2 for the canonical `Episode` contract (core type system).
- Typer for the CLI surface (humans and scripts). Confined to `seahorse.cli`.
- stdio JSON-RPC 2.0 for the MCP agent surface (hand-rolled framing, stdlib-only
  `seahorse.mcp` package — `import seahorse.mcp` does not load Typer).
- `ruamel.yaml` + `python-frontmatter`, confined to the frontmatter adapter.
- **FastEmbed ONNX + onnxruntime** (`embeddings` extra, NOT in the default
  install): the mE5-small bundle defaults to `model_O4.onnx` (fp32, ~235MB) —
  OQ-7-12 verified that no int8/fp16 artifact is portable to Apple Silicon, and
  an open standard must run on Windows/Linux/macOS. A portable int8 bundle is a
  measured follow-up.
- **LiteLLM** (`llm` extra, NOT in the default install): unifies the 100+
  provider surface for the LLM extraction path. Without the extra, `seahorse.llm`
  still imports (contract + `StubLLMClient`) and the real path degrades llm→skip
  with a setup hint.

> The FastAPI / SQLAlchemy / Postgres stack stays in the multi-agent rung
> (Postgres + pgvector). The README states what ships now, not the target
> architecture.

## Design & decisions

Authoritative design lives in the Seahorse Obsidian vault (Fase 5 detailed-design
docs `f5-01` through `f5-16`, plus the F6 open-questions & sign-off register). The
8 blocking contract decisions are signed; ranks 9–15 close inline during F6. See
[CHANGELOG.md](CHANGELOG.md) for release history.

## License

Apache-2.0. See [LICENSE](LICENSE).