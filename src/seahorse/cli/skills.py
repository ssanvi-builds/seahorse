"""``seahorse skill`` — procedural skills CLI.

Parser-agnostic logic for the skill surface: ``add`` (deterministic creation via
``record_procedure``), ``list`` / ``search`` (Discovery — INDEX level with the
procedural filter), ``show`` (Execution — FULL level with the trust gate).
The CLI is a client of the facade (``MemoryFacade``) and the procedural layer —
it never reaches the engine directly (delegation purity).
"""

from __future__ import annotations

from typing import TextIO

from seahorse.cli.output import OutputFormat, render_index_rows, render_message
from seahorse.cli.primitives import _build_provenance, _require_le, _timed
from seahorse.facade.facade import MemoryFacade
from seahorse.procedural.operations import record_procedure
from seahorse.procedural.trust import SkillDelivery, TrustLevel, gate_skill

# The skill Discovery level is the INDEX row (summary ≤ 280 chars, no body).
# ``list`` / ``search`` reuse the facade recall with the procedural filter.
PROCEDURAL = "procedural"


def run_skill_add(
    facade: MemoryFacade,
    *,
    body: str,
    source_type: str = "agent",
    agent_id: str | None = None,
    session_id: str | None = None,
    title: str | None = None,
    trigger: str | None = None,
    scope: str | None = None,
    version: str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse skill add`` — deterministic skill creation (skip path).

    Validates the canonical body (``## Trigger`` / ``## Steps`` /
    ``## Validation`` / ``## Rationale``) via ``record_procedure`` — a
    malformed body surfaces as ``ProceduralError`` (Cat B 96) before any write.
    """
    _require_le(body, limit=32_768, field="body")
    by = _build_provenance(source_type=source_type, agent_id=agent_id, session_id=session_id)
    result = _timed(
        "skill_add",
        lambda: record_procedure(
            facade,
            body=body,
            by=by,
            title=title,
            trigger=trigger,
            scope=scope,
            version=version,
        ),
        verbose=verbose,
    )
    render_message(
        {"ep_id": result.ep_id, "status": "added", "cognitive_type": "procedural"},
        fmt=fmt,
        out=out,
        human_text=f"skill added: ep_id={result.ep_id} (cognitive_type=procedural, skip-first)",
    )


def run_skill_list(
    facade: MemoryFacade,
    *,
    top_k: int = 10,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse skill list`` — Discovery level (procedural filter).

    Uses ``get_vigente`` (all current-state episodes) filtered to
    ``cognitive_type=procedural`` — an honest listing, not a fake query. The
    Discovery row is the episode's summary (≤ 280 chars, no body).
    """
    eps = [
        e
        for e in _timed("skill_list", facade.get_vigente, verbose=verbose)
        if e.cognitive_type == PROCEDURAL
    ]
    eps = sorted(eps, key=lambda e: e.created_at, reverse=True)[:top_k]
    if fmt == "json":
        import json

        out.write(
            json.dumps(
                [
                    {
                        "ep_id": e.id,
                        "subject": e.subject,
                        "summary": e.summary,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in eps
                ]
            )
            + "\n"
        )
        return
    out.write(f"Skills ({len(eps)} results)\n\n")
    if not eps:
        out.write("  (no skills — use `seahorse skill add`)\n")
        return
    for i, e in enumerate(eps, 1):
        out.write(
            f"  {i:<2} {e.id[:36]:<36} {_truncate(e.subject or '-', 28):<28} "
            f"{_truncate(e.summary or '', 40)}\n"
        )
    out.write("\n  Use `seahorse skill show <ep_id>` for the gated body.\n")


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def run_skill_search(
    facade: MemoryFacade,
    *,
    query: str,
    top_k: int = 10,
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse skill search`` — Discovery level (hybrid recall, procedural)."""
    _require_le(query, limit=512, field="query")
    rows = _timed(
        "skill_search",
        lambda: facade.recall(query, k=top_k, cognitive_type=PROCEDURAL),
        verbose=verbose,
    )
    render_index_rows(rows, fmt=fmt, out=out, query=query)


def run_skill_show(
    facade: MemoryFacade,
    *,
    ep_id: str,
    min_trust: str = "medium",
    fmt: OutputFormat = "human",
    out: TextIO,
    verbose: bool = False,
) -> None:
    """``seahorse skill show`` — Execution level (FULL, trust gate).

    The body is delivered with the trust gate applied: a low-trust skill is
    flagged ``as_instruction=False`` (citation/context, not instructions). The
    CLI is a client of the facade (``facade.recall_full``) and applies the gate
    at the CLI layer — it never reaches into the facade's internals
    (delegation purity).
    """
    _require_le(ep_id, limit=64, field="ep-id")
    trust = _parse_trust(min_trust)
    details = _timed(
        "skill_show", lambda: facade.recall_full([ep_id]), verbose=verbose
    )
    if not details:
        out.write("  (no skill found)\n")
        return
    delivery = gate_skill(details[0].episode, min_trust=trust)
    _render_skill_delivery(delivery, fmt=fmt, out=out)


def _parse_trust(value: str) -> TrustLevel:
    try:
        return TrustLevel[value.upper()]
    except KeyError:
        from seahorse.cli.errors import CliUsageError

        raise CliUsageError(
            f"--min-trust {value!r} not in {[t.name.lower() for t in TrustLevel]}"
        ) from None


def _render_skill_delivery(
    delivery: SkillDelivery, *, fmt: OutputFormat, out: TextIO
) -> None:
    if fmt == "json":
        import json

        out.write(
            json.dumps(
                {
                    "ep_id": delivery.ep_id,
                    "trust": delivery.trust.name.lower(),
                    "as_instruction": delivery.as_instruction,
                    "body": delivery.body,
                }
            )
            + "\n"
        )
        return
    mode = "instruction" if delivery.as_instruction else "citation/context (low trust)"
    out.write(f"skill: {delivery.ep_id}  trust={delivery.trust.name.lower()}  mode={mode}\n")
    out.write("---\n")
    out.write(delivery.body or "")
    out.write("\n---\n")


__all__ = [
    "run_skill_add",
    "run_skill_list",
    "run_skill_search",
    "run_skill_show",
]
