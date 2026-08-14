"""Context bootstrap renderer.

A PURE function of ``ContextData``: renders the four bootstrap blocks at INDEX
level, no body. Deterministic — the same data always renders the same text. The
last-session block is an INDEX list, NOT an abstractive summary (honesty:
Seahorse has no session summaries yet). Progressive disclosure: the bootstrap is
compressed (~1-2k tokens); the agent scales to ``recall_full`` when it decides.
"""

from __future__ import annotations

from seahorse.facade.types import ContextData, ContextEpisode

_HEADER = "# Seahorse memory context"
_POINTER = (
    "Use `seahorse recall <query>` / `seahorse recall-full <ep_id>` for details."
)


def _entry(e: ContextEpisode) -> str:
    """One INDEX row: ``- subject — summary`` (no dangling separator)."""
    subject = e.subject or "(no subject)"
    if e.summary:
        return f"- {subject} — {e.summary}"
    return f"- {subject}"


def render_context(data: ContextData) -> str:
    """Render the four bootstrap blocks to text. Pure + deterministic."""
    lines: list[str] = [_HEADER, ""]

    # Block 1: recent episodes (created_at desc, ep_id asc).
    lines.append(f"## Recent episodes ({len(data.recent)})")
    if data.recent:
        lines.extend(_entry(e) for e in data.recent)
    else:
        lines.append("(none yet — the context is empty until episodes are indexed)")
    lines.append("")

    # Block 2: current-state (the recent list IS the current-state set).
    lines.append(f"## Current state ({data.vigente_count} facts)")
    lines.append("The recent list above is the current-state set (created_at desc).")
    lines.append("")

    # Block 3: last session — an INDEX list, NOT an abstractive summary.
    if data.last_session_id:
        lines.append(f"## Last session ({data.last_session_id})")
        lines.extend(_entry(e) for e in data.last_session)
    else:
        lines.append("## Last session")
        lines.append("(none)")
    lines.append("")

    # Block 4: header + counter + pointer.
    lines.append("## Stats")
    lines.append(f"- {data.total_episodes} episodes total")
    lines.append(f"- {_POINTER}")
    return "\n".join(lines)


__all__ = ["render_context"]
