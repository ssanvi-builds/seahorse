"""``seahorse view`` — read-only interactive viewer (ADR-012 l.74).

Closes the daily-utility gap vs claude-mem: a minimal TUI over the existing
facade. READ-ONLY — it never writes, never edits, never calls a write primitive.
It is a client of #12 (MemoryFacade) and reuses the existing render helpers
(delegation purity).

Views (progressive disclosure INDEX → TIMELINE → FULL):
1. Recent episodes (recall INDEX).
2. Search (recall with a query).
3. Timeline of an episode (recall_timeline).
4. Skills (procedural filter).

The interaction loop is stdlib ``input()`` (no curses, no rich) — honest
"mínimo", fully testable via an injected input stream. Without a DB / empty
vault it degrades honestly (ADR-10): a clear message, never a crash.

References:
- adr-012-identity-product-standard.md l.74 (viewer TUI in the free tier)
- obsiforge-evolution-architecture.md l.97/486/579 (daily-utility degradation)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TextIO

from seahorse.cli.output import render_index_rows, render_timeline
from seahorse.facade.facade import MemoryFacade

_MENU = """\
Seahorse viewer (read-only)
  1. recent episodes
  2. search
  3. timeline of an episode
  4. skills
  q. quit
"""


def run_view(
    facade: MemoryFacade,
    *,
    out: TextIO,
    input_stream: Callable[[str], str] | None = None,
) -> None:
    """Interactive read-only viewer loop.

    ``input_stream`` is injectable for tests (a callable that returns the next
    line). The viewer is a client of #12 — it only calls the read primitives
    (``recall`` / ``recall_timeline`` / ``get_vigente``) and never writes.
    """
    read = input_stream if input_stream is not None else input
    if _vault_empty(facade):
        out.write("vault vacío / no inicializado — use `seahorse remember` or `seahorse import`\n")
        return
    while True:
        out.write(_MENU)
        choice = read("> ").strip().lower()
        if choice in ("q", "quit", "exit"):
            out.write("bye\n")
            return
        if choice == "1":
            _view_recent(facade, out)
        elif choice == "2":
            query = read("query: ").strip()
            _view_search(facade, query, out)
        elif choice == "3":
            ep_id = read("ep_id: ").strip()
            _view_timeline(facade, ep_id, out)
        elif choice == "4":
            _view_skills(facade, out)
        else:
            out.write("  unknown option\n")


def _vault_empty(facade: MemoryFacade) -> bool:
    return not facade.get_vigente()


def _view_recent(facade: MemoryFacade, out: TextIO) -> None:
    rows = facade.recall("recent", k=10)
    render_index_rows(rows, fmt="human", out=out, query="recent")


def _view_search(facade: MemoryFacade, query: str, out: TextIO) -> None:
    if not query:
        out.write("  (empty query)\n")
        return
    rows = facade.recall(query, k=10)
    render_index_rows(rows, fmt="human", out=out, query=query)


def _view_timeline(facade: MemoryFacade, ep_id: str, out: TextIO) -> None:
    if not ep_id:
        out.write("  (empty ep_id)\n")
        return
    window = facade.recall_timeline(ep_id)
    render_timeline(window, fmt="human", out=out)


def _view_skills(facade: MemoryFacade, out: TextIO) -> None:
    eps = [e for e in facade.get_vigente() if e.cognitive_type == "procedural"]
    if not eps:
        out.write("  (no skills — use `seahorse skill add`)\n")
        return
    for i, e in enumerate(eps, 1):
        out.write(f"  {i:<2} {e.id[:36]:<36} {_truncate(e.subject or '-', 28)}\n")
    out.write("\n  Use `seahorse skill show <ep_id>` for the gated body.\n")


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


__all__ = ["run_view"]
