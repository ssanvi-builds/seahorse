# Seahorse

[![CI](https://img.shields.io/github/actions/workflow/status/ssanvi-builds/seahorse/ci.yml)](https://github.com/ssanvi-builds/seahorse/actions)
[![License](https://img.shields.io/github/license/ssanvi-builds/seahorse)](LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/seahorse)](https://pypi.org/project/seahorse)
[![PyPI version](https://img.shields.io/pypi/v/seahorse)](https://pypi.org/project/seahorse)

Open standard for persistent, self-evolving LLM agent memory.

> Status: **v0.5.1**. The memory engine works end-to-end from a clean install:
> write episodes, recall them with hybrid semantic retrieval (sqlite-vec kNN +
> FTS5 BM25 fused by RRF), extract with a real multi-LLM path (local-first,
> CI-gated), improve and forget them, and serve an agent over stdio MCP. Recall
> ranks by relevance when vectors are populated and the embedder is wired, and
> honestly degrades to a current-state listing otherwise. `remember` accepts an
> optional `--summary` (deterministic fallback when omitted); `seahorse import`
> migrates claude-mem observations to episodes; and an opt-in recency ranking
> signal is available.
> See [What works](#what-works) and [Reserved](#reserved).

## What it is

A bi-temporal, append-only memory engine for LLM agents that turns episodic notes
into a queryable, conflict-aware, point-in-time-reproducible knowledge base. It is
built so an agent can write thousands of episodes at near-zero cost (the
deterministic skip path is a first-class citizen) and reserve LLM extraction for
the few episodes that justify it.

Every episode is recorded with two time axes — when it became true (`valid_time`)
and when it was recorded (`recording_time`) — so the knowledge base is reproducible
at any past point in time. Supersession and soft-delete are append-only: an
`improve` invalidates the prior episode and supersedes it; a `forget` invalidates
without destroying history.

Every episode is a markdown file with a YAML frontmatter block — human-readable
and editable (including in Obsidian), machine-parseable by agents, and diffable
in git. The format is versioned and documented in
[docs/f3.1-format.md](docs/f3.1-format.md).

## Prerequisites

- **Python ≥ 3.11** (any recent 3.11/3.12/3.13 works). The interpreter's
  `sqlite3` must support `enable_load_extension` (sqlite-vec needs it); most
  standard builds do — `seahorse doctor` reports it as a FAIL if not.
- **uv** — the documented install path is `uv tool install .` (or `uv sync` in a
  checkout). Install it from <https://docs.astral.sh/uv/>. Any other Python
  package manager can install the wheel, but uv is what the Quickstart assumes.
- **Obsidian is optional.** Seahorse runs on any directory of markdown — `seahorse
  init` creates a `.seahorse/` sidecar in a plain folder. Obsidian is a
  human-facing editor for the same folder; its `.obsidian/` directory is ignored
  by Seahorse, never required.

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

# Session capture, context, and consolidation:
# Install the observer (writes [observe] + merges the Claude Code hooks into
# ~/.claude/settings.json):
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

## Migrating a legacy Obsidian vault

A vault of pre-existing Obsidian notes (no frontmatter, or legacy `tags`/`created`
frontmatter) is not yet in the canonical format — `seahorse index rebuild` fails
honestly on those notes. `seahorse frontmatter migrate` converts them (cases
A/B/C/D):

```bash
# Preview: classify every note, write nothing (always exit 0):
seahorse frontmatter migrate --vault myvault --dry-run
# Apply: convert A/B notes, leave C (already canonical) untouched, refuse D (incompatible):
seahorse frontmatter migrate --vault myvault
# Rebuild the sidecar index from the converted notes:
seahorse index rebuild --vault myvault
```

Apply exits `97` (`CLI_MIGRATION_DEFERRED`) when incompatible notes (case D) block
a full migration — the manifest summary is printed first so the operator sees
which notes need manual resolution. `--resume` skips notes unchanged since the
last manifest; `--batch-size` sets the manifest checkpoint cadence. Migration
works before `seahorse init` (it only touches `.md` files + the manifest).

> **First run**: the semantic-embedding model (mE5-small, ~235MB) downloads
> lazily on the first `remember`/`recall` — the CLI announces it so the first
> call doesn't look hung. `seahorse status` shows the active retrieval regime
> (`hybrid RRF (model cached)` vs `current-state listing — install
> seahorse[embeddings] for semantic recall`).

## The agent surface — 7 memory-native primitives + 5 procedural/read-only tools

Exposed over stdio MCP (`io.seahorse.memory/v1`, protocol pinned `2025-11-25`) and
mirrored on the CLI. These are memory primitives, not generic CRUD: an agent calls
`remember` / `recall` / `improve` / `forget` the way a human would talk about memory.

The 7 primitives (write + retrieve):

| Primitive | What it does |
|-----------|--------------|
| `remember` | Record an episode (body, source, optional title/subject). |
| `recall` | INDEX level — the current-state listing, clamped to `top_k`. |
| `recall_timeline` | TIMELINE level — the supersedes chain around an anchor episode. |
| `recall_full` | FULL level — the hydrated episode with all provenance. |
| `improve` | Supersede an episode with a corrected one (append-only). |
| `forget` | Soft-delete an episode (append-only; history preserved). |
| `build_pit` | Build a point-in-time projection (all-None → current state). |

Plus 5 procedural / read-only tools (skills + facade introspection):

| Tool | What it does |
|------|--------------|
| `skill_add` | Create a procedural skill (deterministic, near-zero cost). |
| `skill_show` | Show a skill's gated body (trust gate). |
| `freshness_view` | Freshness snapshot of an episode (age, stale, pending_ingest). |
| `audit_log` | Audit events for an episode (write-path history). |
| `follow_supersedes_chain` | The supersedes closure for an episode (version history). |

Three retrieval levels give **progressive disclosure**: a cheap listing first
(INDEX), the chain on demand (TIMELINE), and the full record only when needed
(FULL). This keeps the common path cheap.

## What works

- Bi-temporal, append-only episode store on stdlib `sqlite3` + sqlite-vec (FTS5
  + vec0). Auto-migrating schema (currently at `schema_version = 10`).
- The 7 memory-native primitives plus 5 procedural / read-only tools, on both
  the CLI and stdio MCP (12 tools total).
- Progressive disclosure (INDEX / TIMELINE / FULL) and point-in-time projection.
- **Hybrid semantic retrieval**: `recall` ranks by relevance — sqlite-vec
  kNN + FTS5 BM25 fused with Reciprocal Rank Fusion (`RRF_K=60`), with PIT
  routing (`state_at` / `known_at`) when a real embedder is wired. The write path
  and `seahorse index rebuild` populate vec0/FTS (best-effort — an embedder
  failure never fails the episode write).
- **Honest degrade**: without the `embeddings` extra (or with no vectors
  populated), `recall` falls back to the current-state listing (score 0.0, no
  ranking) and PIT recall is refused — the engine keeps working without ranking.
- **LLM extraction**: a real multi-LLM path (ollama / gemini / groq /
  openrouter / openai / anthropic / deepseek / vllm, local-first) with a strict
  schema validator + repair loop, retry/fallback chain, and an operative cost cap
  (local and free-tier models price at $0). `seahorse init --llm` bootstraps it;
  the skip-path stays the near-zero-cost default for the bulk of writes.
- **Local-first CI gate**: the real extraction path runs in CI against the
  weakest model of the family (`ollama/qwen3:0.6b`) so the validator + repair
  must carry the load — the path does not silently depend on native structured
  outputs or a strong model.
- Supersession (`improve`) and soft-delete (`forget`) with full history preserved.
- Frontmatter import/export for the Obsidian vault layer (markdown as the
  human-readable, portable on-disk contract). `extraction_mode=consolidated` is a
  schema-valid, round-trippable batch-distillation marker (the value parses and
  round-trips today; the distillation engine is not built yet — see
  [Reserved](#reserved)).
- **Legacy-vault migration**: `seahorse frontmatter migrate` converts legacy
  Obsidian notes (cases A/B/C/D) with a `--dry-run` preview, `--resume`,
  and honest exit `97` when incompatible notes (case D) block a full migration —
  the manifest summary is printed first so the operator sees what needs manual
  resolution.
- Honest exit codes and a structured `{"error": {...}}` envelope on stderr, so
  agents and scripts can branch on `seahorse_code` / `cli_code` deterministically.

## Reserved

The following CLI commands are wired but intentionally return exit `75`
(`CLI_NOT_IN_MVP_0`) with a reason, so the surface is honest about what is not
implemented yet rather than silently no-op'ing:

- `expire`, `revalidate`, `vigentes`, `activos-ahora`, `index verify`.

Batch distillation (`consolidated`) is **schema-valid but not built**: the wire
and frontmatter round-trip the value, but the single-episode write path refuses
it loud and the `distill_episodes` primitive lands in a later milestone.
`llm_partial` stays fully reserved.

The graph-expansion axis of retrieval (BFS into the fusion) is a medium-term
goal — `recall` fuses vector + BM25 + supersedes chain today.

## Stack

- Python ≥ 3.11. stdlib `sqlite3` + sqlite-vec for storage (zero-infra single
  file; the `vec0` virtual table + FTS5 in migration 010).
- numpy for the embedding blob shape.
- Pydantic v2 for the canonical `Episode` contract (core type system).
- Typer for the CLI surface (humans and scripts). Confined to `seahorse.cli`.
- stdio JSON-RPC 2.0 for the MCP agent surface (hand-rolled framing, stdlib-only
  `seahorse.mcp` package — `import seahorse.mcp` does not load Typer).
- `ruamel.yaml` + `python-frontmatter`, confined to the frontmatter adapter.
- **FastEmbed ONNX + onnxruntime** (`embeddings` extra, NOT in the default
  install): the mE5-small bundle defaults to `model_O4.onnx` (fp32, ~235MB) — no
  int8/fp16 artifact is portable to Apple Silicon, and an open standard must run
  on Windows/Linux/macOS. A portable int8 bundle is a measured follow-up.
- **LiteLLM** (`llm` extra, NOT in the default install): unifies the 100+
  provider surface for the LLM extraction path. Without the extra, `seahorse.llm`
  still imports (contract + `StubLLMClient`) and the real path degrades llm→skip
  with a setup hint.

> The FastAPI / SQLAlchemy / Postgres stack is planned for a later multi-agent
> tier (Postgres + pgvector). The README states what ships now, not the target
> architecture.

## Testing

- **Unit + integration**: `uv run pytest` (coverage ≥ 80% gate).
- **Fresh-user e2e**: `scripts/e2e-fresh-user.sh` — the full
  install → init → core CLI → embeddings → LLM → import → MCP flow from a clean,
  isolated HOME (never touches the real `~/.claude` / `~/.claude-mem`).
- **Environment matrix**: `scripts/e2e-matrix.sh` — the fresh-user flow across
  environment combinations (install method × extras × Obsidian × Ollama ×
  online/offline × vault state × concurrency). `--ci-subset` runs the CI-safe
  combos (`core_min` + `uv_sync_dev`); `--list` shows all combos.
- **Core stress**: `scripts/stress-core.sh` — ingest 1000+ episodes, recall
  `--top-k 100` p95 ≤ 250ms (in-process INDEX budget), concurrent single-writer,
  reindex, idempotent import, improve/forget chain.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup, test/lint commands, and pull request workflow. Release history
lives in [CHANGELOG.md](CHANGELOG.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
