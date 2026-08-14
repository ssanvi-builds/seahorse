"""Harness adapters for the observer.

The adapter is the ONLY piece that touches a harness — "Claude Code first is an
adapter, not a binding". The product (engine, the on-disk format, retrieval, MCP,
distill, context) is 100% harness-agnostic; the capture is contained here.
``claude_code.py`` is the first adapter; ``cursor.py``/``codex.py`` are additive
in a later phase (YAGNI — not built).
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
