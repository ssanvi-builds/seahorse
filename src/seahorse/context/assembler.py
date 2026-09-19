"""Context bootstrap renderer.

A PURE function of ``ContextData``: renders the five bootstrap blocks at INDEX
level, no body. Deterministic — the same data always renders the same text. The
last-session block is an INDEX list, NOT an abstractive summary (honesty:
Seahorse has no session summaries yet). Progressive disclosure: the bootstrap is
compressed (~1-2k tokens); the agent scales to ``recall_full`` when it decides.
"""

from __future__ import annotations

from seahorse.facade.types import ContextData, ContextEpisode

_HEADER = "# Seahorse memory context"
_POINTER = (
    "Prefer the `seahorse-mcp` MCP tools (`recall`, `recall_full`) when "
    "available; otherwise `seahorse recall <query>` / `seahorse recall-full "
    "<ep_id>`. Chain `recall_full` (batches of up to 5) on the top hits to "
    "read the full bodies before answering design questions."
)
_KNOWLEDGE_EMPTY = (
    "(none yet — knowledge notes appear as the agent writes project_doc "
    "notes or `seahorse consolidate` distills)"
)
_TOKENS_PER_CHAR = 4  # the chars-per-token estimate convention (never measured)


def _estimate_tokens(text: str) -> int:
    """Estimated tokens in ``text``: chars/4, minimum 1. Always an estimate —
    labeled as such at render time, never presented as a measured count."""
    return max(1, len(text) // _TOKENS_PER_CHAR)


def _entry(e: ContextEpisode) -> str:
    """One INDEX row: ``- subject — summary`` (no dangling separator)."""
    subject = e.subject or "(no subject)"
    if e.summary:
        return f"- {subject} — {e.summary}"
    return f"- {subject}"


def _entry_with_estimate(e: ContextEpisode) -> str:
    """The INDEX row plus its own estimated read cost (chars/4 of the row)."""
    row = _entry(e)
    return f"{row} (~{_estimate_tokens(row)} tok)"


def _knowledge_entry(e: ContextEpisode) -> str:
    """One knowledge row: the INDEX row plus the cognitive type label and its
    own estimated read cost (the estimate covers the whole rendered row)."""
    row = f"{_entry(e)} ({e.cognitive_type or 'knowledge'})"
    return f"{row} (~{_estimate_tokens(row)} tok)"


def render_context(data: ContextData) -> str:
    """Render the five bootstrap blocks to text. Pure + deterministic."""
    lines: list[str] = [_HEADER, ""]

    # Block 1: recent episodes (created_at desc, ep_id asc), each with its own
    # estimated read cost (chars/4 of the row — index-first drill-down by
    # persuasion: the bootstrap is the cheap surface, recall_full is not).
    lines.append(f"## Recent episodes ({len(data.recent)})")
    if data.recent:
        lines.extend(_entry_with_estimate(e) for e in data.recent)
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

    # Block 4: knowledge notes — the distilled surface (consolidated /
    # project_doc), most recent first. INDEX rows; the agent chains
    # recall_full for the bodies (progressive disclosure).
    lines.append(f"## Knowledge notes ({len(data.knowledge)})")
    if data.knowledge:
        lines.extend(_knowledge_entry(e) for e in data.knowledge)
    else:
        lines.append(_KNOWLEDGE_EMPTY)
    lines.append("")

    # Block 5: header + counter + economics + pointer. The bootstrap estimate
    # covers the blocks above (not the Stats block itself); still a pure
    # function of ContextData — identical data renders identical text.
    bootstrap_estimate = _estimate_tokens("\n".join(lines))
    lines.append("## Stats")
    lines.append(f"- {data.total_episodes} episodes total")
    lines.append(
        f"- ~{bootstrap_estimate} tokens to read this bootstrap "
        "(estimate, chars/4); recall_full bodies cost more — drill down selectively"
    )
    lines.append(f"- {_POINTER}")
    return "\n".join(lines)


__all__ = ["render_context"]
