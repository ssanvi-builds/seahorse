"""``seahorse import`` — the vendor migration bridge.

The CLI surface of the importer runner: reads a vendor's data (claude-mem
observations from ``~/.claude-mem/claude-mem.db``, or a ChatGPT export
directory), maps it to canonical episodes via the pure ``import_record``, and
either dry-runs (manifest + projected notes only) or commits (writes via the
facade's ``remember``, near-zero cost). Vendors are NEVER runtime dependencies
— the importer reads their data as a one-time migration source.

Delegation purity: the CLI imports only from ``seahorse.facade`` +
``seahorse.importer``. It does not build episodes, resolve collisions, or touch
the engine directly.
"""

from __future__ import annotations

from typing import Any, TextIO

from seahorse.cli.output import OutputFormat, to_json
from seahorse.facade.facade import MemoryFacade
from seahorse.importer import ChatGPTReader, ClaudeMemReader, ImportRunner

_MODE_HELP = "dry-run (default) maps without writing; commit writes via the facade."


def run_import(
    facade: MemoryFacade,
    *,
    source: str | None,
    vendor: str,
    mode: str,
    project: str | None,
    output_dir: str | None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse import`` — map vendor data to canonical episodes.

    ``vendor`` is ``"claude-mem"`` (default) or ``"chatgpt"``. ``mode`` is
    ``"dry-run"`` (default, safe) or ``"commit"``. The manifest is always
    produced; with ``--output-dir`` it is persisted to
    ``{output_dir}/{run_id}/manifest.json``.
    """
    records, source_meta = _read_records(vendor, source, project)
    runner = ImportRunner(facade, vendor=vendor, output_dir=output_dir)
    manifest = runner.run(records, mode=mode, source=source_meta)
    _render_manifest(manifest, fmt, out)


def _read_records(
    vendor: str, source: str | None, project: str | None
) -> tuple[list[dict], dict[str, Any]]:
    """Read the vendor's records and build the manifest ``source`` descriptor."""
    if vendor == "chatgpt":
        if not source:
            raise ValueError(
                "chatgpt import requires --source pointing at the unpacked "
                "export directory (containing memory.json / conversations.json)"
            )
        chatgpt_reader = ChatGPTReader(source)
        records = chatgpt_reader.iter_records()
        return records, {
            "kind": "export",
            "path": str(chatgpt_reader.source_dir),
            "record_count": len(records),
        }

    claude_reader = ClaudeMemReader(source)
    records = claude_reader.iter_observations(project=project)
    return records, {
        "kind": "sqlite",
        "path": str(claude_reader.db_path),
        "record_count": len(records),
    }


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
