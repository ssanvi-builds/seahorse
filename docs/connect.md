# Connecting your agent

Seahorse is built for agents: the memory surface is a stdio MCP server
(`io.seahorse.memory/v1`) that any MCP-speaking agent can connect to. The same
server, the same 15 tools, the same vault — register it in as many agents as
you use; the vault resolves dynamically at each call (the vault containing the
current working directory, else the per-user default), so one registration
serves every project.

## One command, many agents

```bash
# Default (back-compat): Claude Code only.
seahorse setup

# Register seahorse-mcp in every harness you actually use:
seahorse setup --harness codex,cursor,vscode,antigravity,gemini,claude-code

# Same for uninstall — symmetric, per harness:
seahorse setup --uninstall --harness codex,cursor
```

What `--harness` touches per harness:

| Harness | MCP registration | Instructions block |
|---------|------------------|--------------------|
| `claude-code` (default) | `~/.claude.json` (user scope) | `~/.claude/CLAUDE.md` |
| `codex` | `~/.codex/config.toml` (`[mcp_servers.seahorse-mcp]`) | `~/.codex/AGENTS.md` (global scope) |
| `cursor` | `~/.cursor/mcp.json` | none global — add a User Rule manually |
| `vscode` | `~/.config/Code/User/mcp.json` (`servers`, `"type": "stdio"`) | none global — use a workspace `copilot-instructions.md` |
| `antigravity` | `~/.gemini/config/mcp_config.json` | `~/.gemini/GEMINI.md` (global rules) |
| `gemini` (Gemini CLI) | `~/.gemini/settings.json` | `~/.gemini/GEMINI.md` (global context) |

Guarantees, everywhere (same writers as the Claude Code path):

- **Atomic writes** with a one-time `.seahorse-bak` backup next to the file.
- **Idempotent** — run setup twice, the file is byte-identical.
- **Foreign content preserved verbatim** — your own keys, tables and rules are
  never rewritten; Seahorse only adds or removes its own marked section.
- **A file Seahorse cannot parse is never touched** — you get a WARN and keep
  your config.

`~/.gemini/GEMINI.md` is shared by Gemini CLI and Antigravity (Antigravity's
global rules live there too). The marked block is identical, so installing it
from both harnesses is a no-op the second time.

Every path is overridable for tests and sandboxes: `SEAHORSE_CLAUDE_JSON`,
`SEAHORSE_CODEX_CONFIG`, `SEAHORSE_CURSOR_MCP_JSON`, `SEAHORSE_VSCODE_MCP_JSON`,
`SEAHORSE_ANTIGRAVITY_CONFIG`, `SEAHORSE_GEMINI_SETTINGS`,
`SEAHORSE_CLAUDE_MD`, `SEAHORSE_CODEX_AGENTS_MD`, `SEAHORSE_GEMINI_MD`,
`SEAHORSE_ANTIGRAVITY_MD`.

## One-click installs

Paste these in a browser or run from a shell:

```bash
# Cursor (writes ~/.cursor/mcp.json after you confirm in-app):
open "https://cursor.com/install-mcp?name=seahorse-mcp&config=eyJjb21tYW5kIjoic2VhaG9yc2UtbWNwIiwiYXJncyI6W119"

# VS Code / Copilot (writes the user-scope mcp.json):
code --add-mcp '{"name":"seahorse-mcp","command":"seahorse-mcp"}'
```

After either, the memory tools are available; add the instructions block
below so the agent knows how to use them.

## The instructions block

`seahorse setup --harness <id>` installs a short instruction block teaching
the agent the loop: `recall` before re-discovering, `remember` for durable
facts (with the provenance object call shape), `improve` for corrections,
`skill_add`/`skill_search` for procedures. It is wrapped in HTML-comment
markers, updated in place by later setups, and removed by
`seahorse setup --uninstall` — nothing you wrote around it is touched.

For Cursor and VS Code, copy the block from the next fence into Cursor's
User Rules (Settings → Rules)
or a workspace `.github/copilot-instructions.md`:

```markdown
<!-- seahorse-memory:begin -->
# Persistent memory (Seahorse)

You have a persistent, bi-temporal memory via the `seahorse-mcp` MCP server.
The vault resolves automatically: the vault containing the current working
directory, else the user's default vault.

- **At the start of a task**: if prior context matters (past decisions,
  debugging history, user preferences), use `recall` with a focused query
  before asking the user or re-discovering from scratch.
- **When you learn something durable** — a decision with its rationale, a
  root cause that took real work to find, a preference, a project fact —
  save it with `remember` (concise body, meaningful `title`). Prefer
  `improve` (not a duplicate `remember`) when correcting an existing memory;
  the history is preserved.
  **Call shape for `remember`/`improve`/`forget`**: the `by` parameter is a
  provenance OBJECT with the required keys `agent_id`, `session_id` and
  `source_type` (one of `agent`/`human`/`importer`/`system`) — never a
  string:
  `{"by": {"agent_id": "claude-code", "session_id": "<current session>", "source_type": "agent"}}`.
  Tags are not supported in this release — do not send them.
- **Procedural knowledge** (repeatable workflows, "how we do X") goes in via
  `skill_add`; retrieve it with `skill_search`.
- **Session capture is NOT automatic in this harness** (no hooks): when you
  learn something durable, `remember` it immediately — there is no later
  capture pass. Bootstrap each new session with `context` (the most recent
  valid episodes + the last session's episodes) instead of waiting for
  injection.
- The memory is the user's own Obsidian vault: the human reads and edits the
  same notes. If `recall` returns something that contradicts what the user
  just said, the user is right — correct the memory with `improve`.
<!-- seahorse-memory:end -->
```

This is the exact block `setup --harness` installs for Codex, Gemini CLI and
Antigravity — the capture bullet is the honest, capture-on-intent variant (see
below). For Claude Code the installed block differs only in that bullet
(automatic capture, hooks + observer).

## Honest capture: what is automatic, what is not

- **Automatic session capture** (hooks + observer → every session lands in the
  vault without the agent doing anything) exists for **Claude Code** only.
  That is why `seahorse setup` (default) also installs Claude Code hooks.
- **Every other harness** uses the memory through the MCP tools: the agent
  calls `remember` when it learns something durable and `context` at session
  start. The instructions block above teaches exactly that. It is real,
  honest memory — just capture-on-intent rather than capture-always.

You can verify the whole chain any time:

```bash
seahorse doctor --fix
```

## Listings

The manifests for the MCP registry, Smithery and mcpm live in the repo — see
[docs/registries/README.md](registries/README.md) for what ships and how each
listing is published.