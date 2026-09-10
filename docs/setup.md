# Setup reference

`seahorse setup` is the whole onboarding: one command that configures a vault
(portable `~/seahorse-mem` default), the database, Claude Code capture hooks, a
running observer, the user-scope MCP registration, the agent instructions
block, the packaged agent skills, and a self-tested LLM provider when one is
available. It always exits 0 — every step that cannot complete degrades to a
WARN line with the exact fix, instead of failing. Verify with `seahorse
--version` and `seahorse doctor`.

Every step is opt-out-able; every step is idempotent — run setup twice,
nothing duplicates.

| Flag | Effect |
|------|--------|
| `--vault <path>` | Use (and bootstrap) this vault instead of resolution. |
| `--harness <ids>` | Comma-separated MCP destinations (default `claude-code`). See [connect.md](connect.md). |
| `--no-mcp` | Skip the user-scope MCP registration. |
| `--no-agent-instructions` | Skip the instructions blocks (per harness). |
| `--no-skills` | Skip installing the packaged agent skills. |
| `--skip-llm` | Skip provider detection + live self-test. |
| `--warm-embeddings` | Pre-download the embeddings model (~235MB). |
| `--auto-consolidate` | Opt in: distill at the Claude Code Stop event. |
| `--uninstall` | Symmetric removal (see below). |

Without a terminal (an agent running onboarding unattended), setup bootstraps
the portable per-user default `~/seahorse-mem` instead of failing — it never
hits a cold-start prompt. On a terminal it offers an interactive picker of
your registered Obsidian vaults.

## LLM provider

Setup detects providers in preference order — local Ollama (qwen3 first), then
cloud keys present in the environment (Gemini, Groq, OpenRouter, OpenAI,
Anthropic, DeepSeek) — and writes the `[llm]` config only after a passing live
self-test, so `seahorse doctor` never reports a default that was never
verified. If nothing passes, an interactive terminal offers to pull a local
Ollama model, paste a cloud API key, or skip (the default — big downloads
never happen on a bare Enter).

A pasted key is stored in the credentials store:
`~/.config/seahorse/credentials.json`, mode 0600, atomic writes, never in
`seahorse.toml`, values never printed. Headless commands and `seahorse doctor`
load the store automatically; pre-existing environment variables win.

## Uninstall

`seahorse setup --uninstall` removes the hooks, the `[observe]` section, the
MCP registration, the instructions blocks, and the packaged skills, and stops
the observer. With `--harness` it removes exactly what that harness selection
installed. The vault, its notes, `[materialize]`, and the global pointer stay
— your memory is yours.

## What ships now, and what is intentionally reserved

These behavior details are part of the shipped surface; they are documented
here because they are reference material, not onboarding material.

- **Decay ranking is opt-in and default-off.** When enabled, a FAMA-style
  Ebbinghaus forgetting curve downweights stale knowledge by age
  (`score' = score · 2^(-age/half_life)`), with per-`cognitive_type` half-life
  priors (episodic 139d, semantic 347d, social 231d, procedural 347d). Off by
  default: the pure-RRF recall fingerprint stays bit-comparable — a
  reproducibility property the benchmark harness depends on.
- **Cross-encoder rerank was measured and rejected.** It degraded recall@10
  (0.13 → 0.11) with 1.2s latency; the retrieval path ships without it
  ([benchmark.md](benchmark.md) has the numbers and the decision).
- **Honest unimplemented surface.** A few CLI commands are wired but
  intentionally return exit `75` with a reason (`expire`, `revalidate`,
  `index verify`), so the surface is honest about what is not implemented yet
  rather than silently no-op'ing. `llm_partial` stays fully reserved.
- **Multi-provider LLM extraction** (`seahorse-memory[llm]`, LiteLLM):
  ollama / gemini / groq / openrouter / openai / anthropic / deepseek / vllm,
  local-first, with a strict schema validator + repair loop, retry/fallback
  chain, and an operative cost cap (local and free-tier models price at $0).
  Without the extra, `seahorse.llm` still imports (contract + `StubLLMClient`)
  and the real path degrades llm→skip with a setup hint. CI runs the real
  extraction path against the weakest model of the family
  (`ollama/qwen3:0.6b`) so the validator + repair must carry the load.
- **Embeddings bundle** (`seahorse-memory[embeddings]`): the mE5-small
  FastEmbed/ONNX bundle defaults to `model_O4.onnx` (fp32, ~235MB) — no
  int8/fp16 artifact is portable to Apple Silicon, and an open standard must
  run on Windows/Linux/macOS. A portable int8 bundle is a measured follow-up.
- The FastAPI / SQLAlchemy / Postgres stack is planned for a later multi-agent
  tier (Postgres + pgvector). What ships now is the local-first stack above.