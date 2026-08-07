"""``seahorse import`` — the claude-mem migration bridge (#15, f5-15).

The CLI surface of the importer runner: reads claude-mem observations
(``~/.claude-mem/claude-mem.db`` by default), maps them to F3.1 episodes via the
pure ``import_record``, and either dry-runs (manifest + projected notes only) or
commits (writes via ``#12.remember``, ADR-09). claude-mem is NEVER a runtime
dependency — the importer reads its SQLite as a one-time migration source.

Delegation purity (f5-14 §1): the CLI imports only from ``seahorse.facade`` +
``seahorse.importer``. It does not build episodes, resolve collisions, or touch
the engine directly.
"""

from __future__ import annotations

from typing import Any, TextIO

from seahorse.cli.output import OutputFormat, to_json
from seahorse.facade.facade import MemoryFacade
from seahorse.importer import ClaudeMemReader, ImportRunner

_MODE_HELP = "dry-run (default) maps without writing; commit writes via the facade."


def run_import(
    facade: MemoryFacade,
    *,
    source: str | None,
    mode: str,
    project: str | None,
    output_dir: str | None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse import`` — map claude-mem observations to F3.1 episodes.

    ``mode`` is ``"dry-run"`` (default, safe) or ``"commit"``. The manifest is
    always produced; with ``--output-dir`` it is persisted to
    ``{output_dir}/{run_id}/manifest.json``.
    """
    reader = ClaudeMemReader(source)
    records = reader.iter_observations(project=project)
    runner = ImportRunner(facade, output_dir=output_dir)
    manifest = runner.run(
        records,
        mode=mode,
        source={
            "kind": "sqlite",
            "path": str(reader.db_path),
            "record_count": len(records),
        },
    )
    _render_manifest(manifest, fmt, out)


def _render_manifest(manifest: Any, fmt: OutputFormat, out: TextIO) -> None:
    """Render the batch manifest (human summary or full JSON)."""
    if fmt in ("json", "jsonl"):
        out.write(to_json(manifest.to_dict()) + "\n")
        return
    agg = manifest.aggregate
    out.write(
        f"import {manifest.mode} run_id={manifest.run_id} vendor={manifest.vendor}\n"
        f"  records_read={agg['records_read']} notes_emitted={agg['notes_emitted']} "
        f"noop_discarded={agg['noop_discarded']} failures={agg['failures']} "
        f"skipped_collision={agg['skipped_collision']} "
        f"skipped_idempotent={agg['skipped_idempotent']}\n"
    )
    if manifest.integrity_ok:
        out.write("  integrity_ok=true\n")
    else:
        out.write("  integrity_ok=false (failures > 0)\n")


__all__ = ["run_import"]
