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

## Codex: automatic capture too

Codex's hooks (GA May 2026) speak the same stdin-JSON contract as Claude Code,
so `seahorse setup --harness codex` installs the **same capture command** in
`~/.codex/hooks.json` — sessions land in the vault without Codex doing
anything, and the SessionStart hook injects your memory as bootstrap context.

One step the installer cannot automate: Codex skips hooks it does not trust
yet. Approve the Seahorse hooks **once** via `/hooks` (hash-based trust
review) or capture stays off — `seahorse doctor` calls this out with its
`capture_health` check (hooks installed but 0 episodes in the last 7 days).

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

This is the exact block `setup --harness` installs for Gemini CLI and
Antigravity — the capture bullet is the honest, capture-on-intent variant (see
below). For Claude Code and Codex the installed block differs only in that
bullet (automatic capture, hooks + observer; Codex's adds the one-time
`/hooks` trust line).

## Honest capture: what is automatic, what is not

- **Automatic session capture** (hooks + observer → every session lands in the
  vault without the agent doing anything) exists for **Claude Code and Codex**
  (Claude Code hooks in `settings.json`, Codex hooks in `hooks.json` — the
  same capture command in both). That is why `seahorse setup` (default) also
  installs Claude Code hooks.
- **Every other harness** uses the memory through the MCP tools: the agent
  calls `remember` when it learns something durable and `context` at session
  start. The instructions block above teaches exactly that. It is real,
  honest memory — just capture-on-intent rather than capture-always.

You can verify the whole chain any time:

```bash
seahorse doctor --fix
```

## Remote access (Streamable HTTP)

Apps without child processes — Claude.ai, ChatGPT, Gemini — cannot speak
stdio. For them the same `io.seahorse.memory/v1` server also serves Streamable
HTTP (MCP spec `2025-11-25`): same 15 tools, same vault, byte-identical
responses — a different wire. Use it only when stdio is impossible; locally,
stdio is simpler and needs no token.

```bash
# Start the remote server (binds 127.0.0.1; --port 0 = ephemeral port):
seahorse mcp --transport http --port 0

# Same server, no install:
uvx --from seahorse-memory seahorse-mcp --transport http --port 0

# It prints the bound URL on stderr:
#   seahorse-mcp: listening on http://127.0.0.1:49896
```

The bearer token is required — always. `seahorse setup` generates one into
the vault's `[http]` section (`<vault>/.seahorse/seahorse.toml`), so after
setup the server starts with no further configuration; `SEAHORSE_HTTP_TOKEN`
overrides it, and without any token the server exits before binding (exit
code 99).

Register it in Claude Code (the URL and name first, `--header` last — it is a
variadic option):

```bash
claude mcp add --transport http seahorse-remote \
  http://127.0.0.1:8767/mcp \
  --header "Authorization: Bearer $SEAHORSE_HTTP_TOKEN"
```

Every POST carries the token; anything else gets `401`. The surface is
POST-only: id'd requests answer `200`, notifications `202`, GET/DELETE answer
`405` — there is no SSE stream. The server is **stateless JSON mode**: no
`Mcp-Session-Id` is issued and every request is self-contained; clients that
require a session handshake are out of scope. Also enforced per request:
per-IP rate limiting (`429`), a 256 KiB body cap (`413`), Origin checking
(`403`) and protocol-version validation (`400`).

### Beyond loopback

Remote consumers need an HTTPS URL, and the server does no TLS itself — bind
loopback and put a reverse proxy or tunnel in front. The `seahorse remote`
wizard automates the whole path and prints paste-ready blocks per app:

```bash
seahorse remote start    # server daemon + cloudflared quick tunnel; prints
                         # the public /mcp URL, the token and per-app blocks
seahorse remote status   # reprints current state and the instructions
seahorse remote stop     # closes the tunnel first, then the server
```

The wizard's logs (`<vault>/.seahorse/remote/`) carry the listen line and any
server crash, but the HTTP server does not log individual requests — quiet by
design. Manually, if you prefer two terminals:

```bash
cloudflared tunnel --url http://127.0.0.1:8767   # or: ngrok http 127.0.0.1:8767
```

**Warning**: behind a tunnel the bearer token becomes the *only* barrier
between the internet and your vault. Use a long token, keep the tunnel URL
private, and let the proxy terminate TLS. The in-memory rate limit resets on
restart — it deters abuse; it is not an access-control layer.

#### Quick-tunnel readiness: URL printed ≠ URL reachable

Observed on live quick tunnels (2026-10-01 through 2026-10-06):

- The hostname's DNS record can take **minutes** to appear globally (~5 min
  on one start, ~5 s on the next). The wizard reports the URL as soon as
  cloudflared prints it — a consumer that cannot connect right away should
  retry after a minute or two.
- In the first seconds after the URL appears, the edge may answer
  **530 (Cloudflare error 1033)** while the tunnel connections register.
  Retry.
- The quick-tunnel URL changes on every restart — reconnect the consumer.
  Named tunnels (a Cloudflare account plus DNS) are the stable alternative.
- The hostname itself **dies server-side after roughly a day** while the
  cloudflared process stays up: it logs `Unauthorized: Tunnel not found` in
  an endless retry loop and the DNS record is gone. Observed three times
  (two tunnels from 2026-10-03 found dead on 2026-10-05; a fresh one from
  2026-10-05 dead ~20 h later). Two consequences: a live `pid` does not
  mean the URL works (the wizard's reuse check cannot see this), and a
  connection that worked yesterday may need the wizard re-run — quick
  tunnels are a "spin up fresh, use now" tool, not an always-on endpoint.

#### What real clients do on this wire

Verified with claude-code 2.1.280 over a public tunnel (2026-10-01):

- It opens with a `server/discover` probe at protocol version `2026-07-28`.
  The strict per-request version check answers
  `400 unsupported MCP-Protocol-Version` and the client degrades cleanly to
  `initialize` (`2025-11-25`) — strictness does not break real clients.
- One `GET /mcp` probe answers `405` (POST-only surface); the client
  continues normally.
- No `Origin` header is sent — the `403` Origin rule does not trigger for
  CLI clients. Server-side web consumers may differ; not yet observed.
- Everything arrives from loopback: the per-IP rate limiter sees a single
  bucket behind the tunnel. Cloudflare preserves the original client IP in
  `Cf-Connecting-Ip` should per-origin limiting ever be needed.

The paste-ready connection blocks for ChatGPT, Gemini (web and CLI) and
Claude Code come from `seahorse remote start` — one source, never
hand-copied into this file. Each block carries its own verification status:
ChatGPT is documented from official sources but **not verified on a live
account** (it requires a paid plan); Gemini web is a live-verified negative —
consumer Gemini has no bearer-token path (OAuth-only, 2026-10-05), so
bearer-only Seahorse cannot register there; Claude Code is verified over a
live tunnel (2026-10-01, claude-code 2.1.280); the Gemini CLI roundtrip is
deferred to the v1.8.0 provider-testing round (the Antigravity client hit
an account-eligibility loop on 2026-10-06 — findings recorded, verification
re-opened for v1.8.0).

## Listings

The manifests for the MCP registry, Smithery and mcpm live in the repo — see
[docs/registries/README.md](registries/README.md) for what ships and how each
listing is published.