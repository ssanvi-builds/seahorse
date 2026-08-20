# Seahorse — Roadmap

Seahorse is an open standard for persistent, self-evolving LLM agent memory. This
roadmap describes the product from a user and contributor perspective. Release
history lives in [CHANGELOG.md](CHANGELOG.md).

## Current state

What works today (v0.9.0):

- **Distribution on PyPI as `seahorse-memory`** — the name `seahorse` was taken
  by an unrelated project, so the distribution is published as `seahorse-memory`
  (`pip install seahorse-memory`). The import package and the `seahorse` /
  `seahorse-mcp` console scripts are unchanged.
- **Agent-first MCP surface** — register the server in any MCP agent
  (`claude mcp add seahorse-mcp -- uvx --from seahorse-memory seahorse-mcp --vault "${HOME}/myvault"` or `.mcp.json`); the agent sees 14 memory tools.
- **Bi-temporal, append-only memory engine** — every episode carries both when it
  became true (`valid_at`) and when it was recorded (`created_at`), so the
  knowledge base is reproducible at any past point in time. Supersession
  (`improve`) and soft-delete (`forget`) are append-only: history is never
  destroyed.
- **CLI + MCP agent surface** — 7 memory-native primitives (`remember`, `recall`,
  `recall_timeline`, `recall_full`, `improve`, `forget`, `build_pit`) plus 7
  procedural/read-only tools, served over stdio MCP (`io.seahorse.memory/v1`) and
  mirrored on the CLI (the same skill and read-only surfaces on both sides).
- **Hybrid semantic retrieval** — `recall` ranks by relevance (sqlite-vec kNN +
  FTS5 BM25 fused with Reciprocal Rank Fusion) with point-in-time routing, and
  honestly degrades to a current-state listing when no embeddings are available.
- **Decay ranking bias (opt-in, default-off)** — a FAMA-style Ebbinghaus
  forgetting curve (`score' = score · 2^(-age_days/half_life[type])`, factor in
  `(0, 1]`) that downweights stale knowledge by `created_at` age, with S₀
  half-life priors per `cognitive_type` (episodic 139d, semantic 347d, social
  231d, procedural 347d). Composable via the `build_facade(decay=...)`
  composition-root swap, `seahorse benchmark --decay-half-life`, or the
  `mvp1_decay` benchmark variant. PIT queries reproduce state as-of-`t` with
  pure RRF — the bias never crosses axes (ADR-03) and the read path never
  writes (R2).
- **LLM extraction** — a real multi-provider extraction path (local-first:
  Ollama, plus Gemini, Groq, OpenRouter, OpenAI, Anthropic, DeepSeek, vLLM) with
  strict schema validation, retry/fallback, and an operative cost cap. The
  skip-path (deterministic, near-zero-cost) remains the default for the bulk of
  writes.
- **Session capture and distillation** — `seahorse setup` installs a session
  observer (Claude Code), `seahorse context` bootstraps context for the next
  session, and `seahorse consolidate` distills recurrent episodes into semantic
  knowledge notes. With `--synthesis llm` (or the `[distill]` config section),
  the distillation is LLM-synthesized: 1 call per cluster turns N episodes into
  one coherent fact, degrading honestly to the deterministic fallback on
  failure. **Supersession** (opt-in via `[distill] supersede` or
  `seahorse consolidate --supersede`) updates an existing consolidated note in
  place via `improve` when a cluster gains new valid episodes — never silently
  overriding a note a human has edited.
- **Vault migration and import** — `seahorse frontmatter migrate` converts legacy
  Obsidian notes, and `seahorse import` migrates claude-mem observations into
  episodes.
- **Procedural skills, graph retrieval, and a read-only viewer** — deterministic
  skill authoring, a BFS timeline axis, and an interactive TUI. Timelines can
  also be ranged by `created_at`/`valid_at` around an anchor, and `--verbose`
  reports per-operation timing.
- **Benchmark harness** — a reproducible retrieval/QA harness (LMEB-S) with
  fingerprint-pinned runs, used to make retrieval decisions with data. The
  current numbers and methodology are published in
  [docs/benchmark.md](docs/benchmark.md), with caveats and reproduction commands.

CI runs the full test suite with a coverage gate (≥80%), plus lint and type
checks. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to build and test locally.

## Roadmap

- **Near term** — close the "self-evolving" loop and harden the moat. The
  public launch (v0.6.0) is done; the focus is responding to launch feedback
  (Show HN, community), reliability, edge cases, and making external
  contribution straightforward. Decay (Sprint D) is implemented default-off; the
  remaining near-term work is **retrieval quality** as a first-class workstream —
  the published benchmark is the public face of the product — plus the
  authoritative LMEB-S runs (A5/A6/A4/recency/decay) that calibrate the recency
  and decay pins. The F7 experiments (multi-hop, entity-centric, decay, skills)
  and the roadmap-review experiments (RRF_K sweep, rerank-with-body, end-to-end
  measurement) are wired into the harness (`seahorse benchmark experiment`); the
  synthetic runs validate the mechanics, and the authoritative LMEB-S runs
  inform the roadmap.
- **v1.0 — the stable standard** — the milestone that promises not to break
  whoever depends on Seahorse. Three gates: (1) **frozen contracts** — the F3.1
  portable format (schema 1.0.0) and the MCP profile `io.seahorse.memory/v1`
  (14 tools) stop changing in breaking ways; additive evolution only, with a
  documented migration path for 0.x vaults; (2) **the self-evolving loop
  validated with data** — observe → consolidate/supersede → recall → decay
  working end-to-end, with an authoritative LMEB-S run published as the official
  baseline in `docs/benchmark.md` and the decay/recency pins decided by that
  evidence (calibrated or documented `keep_off`); (3) **release quality** — CI
  green, ≥80% coverage, fresh-user e2e green. Deliberately local-first: the
  remote MCP server (Streamable HTTP), web dashboard, and managed sync are Fase 2
  expansions gated by adoption, not requirements for 1.0. After 1.0, a breaking
  change is a 2.0.
- **Medium term** — the Fase 2 re-sequenced: the remote MCP server (Streamable
  HTTP) as a standard expansion that also serves the local free tier, with the
  web dashboard and managed sync deferred until the adoption gate produces data.
  Cross-project sync and the web viewer follow that gate.
- **Long term** — a managed cloud offering as a later phase, gated by adoption,
  and wider adoption of the memory standard across agents and harnesses.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup, test/lint commands, and pull request workflow. Security
issues should be reported through the process in [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
