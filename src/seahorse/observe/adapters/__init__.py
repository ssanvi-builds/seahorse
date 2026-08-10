"""Harness adapters for the observer (obsiforge §4.2).

The adapter is the ONLY piece that touches a harness — "Claude Code primero es
un adapter, no un binding". The product (engine, F3.1, retrieval, MCP, distill,
context) is 100% harness-agnostic; the capture is contained here. ``claude_code.py``
is the first adapter; ``cursor.py``/``codex.py`` are additive in phase 2 (YAGNI —
not built).
"""

from seahorse.observe.adapters.claude_code import (
    handle_post_tool_use,
    handle_session_start,
    handle_stop,
    handle_user_prompt_submit,
)

__all__ = [
    "handle_post_tool_use",
    "handle_session_start",
    "handle_stop",
    "handle_user_prompt_submit",
]
