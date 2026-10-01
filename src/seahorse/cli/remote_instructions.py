"""Per-app paste-ready connection instructions — the single source.

The same strings back the CLI (human + ``--json``) and ``docs/connect.md``;
building them here means the three surfaces cannot drift. Every block is
copy-pasteable by a non-technical user: the public MCP URL and the vault's
bearer token go exactly where the app's UI expects them.

The Gemini web block is an honest placeholder until the connector experiment
defines where a consumer custom app accepts credentials (if it does at all) —
invented instructions are worse than none.
"""

from __future__ import annotations

SECURITY_WARNING = (
    "This public URL is live: anyone with the URL AND the token can read and "
    "write your memory. The token is the only barrier — treat it like a "
    "password. The quick-tunnel URL changes on every restart; stop exposure "
    "with `seahorse remote stop`."
)

# ChatGPT connectors require a paid plan. This block is written from OpenAI's
# official connector documentation and marked NOT verified on a live account
# (none was available at authoring time); update the note after a real
# verification.
CHATGPT_UNVERIFIED = (
    "not verified on a live account — requires a paid ChatGPT plan "
    "(Plus/Pro/Business/Enterprise/Edu)"
)

# The consumer Gemini web app's custom-app credential mechanism is under
# verification (connector experiment pending); the block stays honest until
# that experiment lands (see docs/connect.md).
GEMINI_WEB_PENDING = (
    "credential mechanism under verification — see docs/connect.md; the "
    "Gemini CLI works today (block below)"
)

APP_KEYS = ("chatgpt", "gemini_web", "gemini_cli", "claude_code")

# CLI ``--app`` choice → instruction keys ("all" → every block).
_APP_CHOICE_KEYS = {
    "all": APP_KEYS,
    "chatgpt": ("chatgpt",),
    "gemini": ("gemini_web",),
    "gemini-cli": ("gemini_cli",),
    "claude-code": ("claude_code",),
}

# The CLI ``--app`` boundary validates against this tuple (exit 2 on a bad
# choice) — the same source ``instruction_blocks`` filters by above.
APP_CHOICES = tuple(_APP_CHOICE_KEYS)


def chatgpt_block(mcp_url: str, token: str) -> str:
    """ChatGPT web connector (Developer mode; Token auth)."""
    return (
        "— ChatGPT (web; Developer mode; paid plan) —\n"
        "  1. chatgpt.com → Settings → Connectors → enable Developer mode\n"
        "  2. Apps & Connectors → Create a new app\n"
        f"  3. MCP server URL:  {mcp_url}\n"
        f"  4. Authentication:  Token → paste: {token}\n"
        "  5. Scan Tools (expect 15 tools), then pick the app in a chat.\n"
        f"  Note: {CHATGPT_UNVERIFIED}."
    )


def gemini_web_block(mcp_url: str, _token: str) -> str:
    """Consumer Gemini web custom app — honest placeholder pre-experiment.

    The token is deliberately unused (``_token``): the experiment must first
    define where a consumer custom app accepts credentials at all.
    """
    return (
        "— Gemini (web app) —\n"
        "  gemini.google.com → Settings → Connected Apps → Add a custom app\n"
        f"  MCP server URL:  {mcp_url}\n"
        f"  Credentials: {GEMINI_WEB_PENDING}."
    )


def gemini_cli_block(mcp_url: str, token: str) -> str:
    """Gemini CLI remote registration (works today)."""
    return (
        "— Gemini CLI —\n"
        "  gemini mcp add --transport http seahorse "
        f'{mcp_url} --header "Authorization: Bearer {token}"'
    )


def claude_code_block(mcp_url: str, token: str) -> str:
    """Claude Code remote registration (``--header`` LAST — it is variadic)."""
    return (
        "— Claude Code —\n"
        "  claude mcp add -s user --transport http seahorse-remote "
        f'{mcp_url} --header "Authorization: Bearer {token}"'
    )


_BUILDERS = {
    "chatgpt": chatgpt_block,
    "gemini_web": gemini_web_block,
    "gemini_cli": gemini_cli_block,
    "claude_code": claude_code_block,
}


def instruction_blocks(app: str, *, mcp_url: str, token: str) -> dict[str, str]:
    """The selected apps' instruction blocks keyed by instruction key."""
    try:
        keys = _APP_CHOICE_KEYS[app]
    except KeyError as exc:
        raise ValueError(f"unknown app choice: {app!r}") from exc
    return {key: _BUILDERS[key](mcp_url, token) for key in keys}


def human_instructions(app: str, *, mcp_url: str, token: str) -> str:
    """The selected blocks for the terminal, blank-line separated."""
    return "\n\n".join(instruction_blocks(app, mcp_url=mcp_url, token=token).values())


__all__ = [
    "SECURITY_WARNING",
    "CHATGPT_UNVERIFIED",
    "GEMINI_WEB_PENDING",
    "APP_KEYS",
    "APP_CHOICES",
    "chatgpt_block",
    "gemini_web_block",
    "gemini_cli_block",
    "claude_code_block",
    "instruction_blocks",
    "human_instructions",
]