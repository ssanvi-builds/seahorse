# Seahorse — Roadmap

Seahorse is an open standard for persistent, self-evolving LLM agent memory. This
roadmap describes the product from a user and contributor perspective. Release
history lives in [CHANGELOG.md](CHANGELOG.md).

## Current state

What works today (v0.6.0):

- **Distribution on PyPI as `seahorse-memory`** — the name `seahorse` was taken
  by an unrelated project, so the distribution is published as `seahorse-memory`
  (`pip install seahorse-memory`). The import package and the `seahorse` /
  `seahorse-mcp` console scripts are unchanged.
- **Agent-first MCP surface** — register the server in any MCP agent
  (`claude mcp add seahorse-mcp -- uvx --from seahorse-memory seahorse-mcp --vault "${HOME}/myvault"` or `.mcp.json`); the agent sees 12 memory tools.
- **Bi-temporal, append-only memory engine** — every episode carries both when it
  became true (`valid_at`) and when it was recorded (`created_at`), so the
  knowledge base is reproducible at any past point in time. Supersession
  (`improve`) and soft-delete (`forget`) are append-only: history is never
  destroyed.
- **CLI + MCP agent surface** — 7 memory-native primitives (`remember`, `recall`,
  `recall_timeline`, `recall_full`, `improve`, `forget`, `build_pit`) plus 5
  procedural/read-only tools, served over stdio MCP (`io.seahorse.memory/v1`) and
  mirrored on the CLI.
- **Hybrid semantic retrieval** — `recall` ranks by relevance (sqlite-vec kNN +
  FTS5 BM25 fused with Reciprocal Rank Fusion) with point-in-time routing, and
  honestly degrades to a current-state listing when no embeddings are available.
- **LLM extraction** — a real multi-provider extraction path (local-first:
  Ollama, plus Gemini, Groq, OpenRouter, OpenAI, Anthropic, DeepSeek, vLLM) with
  strict schema validation, retry/fallback, and an operative cost cap. The
  skip-path (deterministic, near-zero-cost) remains the default for the bulk of
  writes.
- **Session capture and distillation** — `seahorse setup` installs a session
  observer (Claude Code), `seahorse context` bootstraps context for the next
  session, and `seahorse consolidate` distills recurrent episodes into semantic
  knowledge notes.
- **Vault migration and import** — `seahorse frontmatter migrate` converts legacy
  Obsidian notes, and `seahorse import` migrates claude-mem observations into
  episodes.
- **Procedural skills, graph retrieval, and a read-only viewer** — deterministic
  skill authoring, a BFS timeline axis, and an interactive TUI.
- **Benchmark harness** — a reproducible retrieval/QA harness (LMEB-S) with
  fingerprint-pinned runs, used to make retrieval decisions with data. The
  current numbers and methodology are published in
  [docs/benchmark.md](docs/benchmark.md), with caveats and reproduction commands.

CI runs the full test suite with a coverage gate (≥80%), plus lint and type
checks. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to build and test locally.

## Roadmap

- **Near term** — public launch and community onboarding. The engine is
  feature-complete for a single-user memory store; the focus is the launch
  (Show HN, blog, X, MCP registry, Obsidian community), reliability, edge
  cases, and making external contribution straightforward.
- **Medium term** — distillation synthesis, cross-project sync, and a web
  viewer.
- **Long term** — a managed cloud offering as a later phase, and wider adoption
  of the memory standard across agents and harnesses.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup, test/lint commands, and pull request workflow. Security
issues should be reported through the process in [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
