"""Importer runner — the operational ingestion driver.

Two layers: the pure mapping (``import_record``) and this driver. The runner
iterates the vendor records, maps each to F3.1 notes, and delegates to the
write-path via ``facade.remember`` (the single entry point). It owns the
manifest, dry-run/commit modes, idempotency (deterministic UUIDv5 -> NOOP on
re-import), and collision handling (``WriteResult.collisions_detected`` is
RETURNED, never raised).

Modes: ``dry-run`` maps without writing (manifest + projected notes only);
``commit`` writes via the facade. Both always emit the manifest.

Collision policy: default ``skip`` preserves the existing episode
(``status=skipped_collision``). The importer NEVER auto-resolves a collision
against a non-imported fact (authority).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.facade.types import Provenance, RememberPayload
from seahorse.importer.claude_mem import import_record
from seahorse.importer.types import ImportItem, ImportManifest

_logger = logging.getLogger("seahorse.importer.runner")

# WriteResult statuses -> manifest item status.
_STATUS_COMMITTED = "committed"
_STATUS_IDEMPOTENT = "skipped_idempotent"
_STATUS_COLLISION = "skipped_collision"
_STATUS_FAILED = "failed"
_STATUS_NOOP = "noop"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _make_run_id(vendor: str) -> str:
    """Deterministic-ish run id: ``imp_{ts}_{vendor}_{short}``."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"imp_{ts}_{vendor}"


class ImportRunner:
    """Ingestion driver over ``facade.remember`` (the single entry point).

    ``facade`` is the ``MemoryFacade`` (or a recording double in tests).
    ``output_dir`` is where the manifest lives (default
    ``{vault}/.seahorse/imports/{run_id}/``); the runner creates it.
    """

    def __init__(
        self,
        facade: Any,
        *,
        run_id: str | None = None,
        output_dir: Path | str | None = None,
        on_collision: str = "skip",
    ) -> None:
        self._facade = facade
        self._run_id = run_id or _make_run_id("claude-mem")
        self._on_collision = on_collision
        self._output_dir = Path(output_dir) if output_dir is not None else None

    # ------------------------------------------------------------------ run

    def run(
        self,
        records: list[dict],
        *,
        mode: str = "dry-run",
        source: dict[str, Any] | None = None,
    ) -> ImportManifest:
        """Map + (optionally) ingest every record; return the batch manifest.

        ``mode`` is ``"dry-run"`` (map only, no store writes) or ``"commit"``
        (write via the facade). ``source`` describes the vendor source for the
        manifest (e.g. ``{"kind": "sqlite", "path": ..., "record_count": N}``).
        """
        if mode not in ("dry-run", "commit"):
            raise ValueError(f"mode must be 'dry-run' or 'commit', got {mode!r}")
        started = _now_iso()
        items: list[ImportItem] = []
        aggregate: Counter[str] = Counter()
        losses: Counter[str] = Counter()

        for seq, record in enumerate(records, start=1):
            item = self._process_record(record, seq, mode, aggregate, losses)
            items.append(item)

        manifest = ImportManifest(
            run_id=self._run_id,
            mode=mode,
            started_at=started,
            ended_at=_now_iso(),
            source=source or {"kind": "unknown", "record_count": len(records)},
            aggregate={
                "records_read": len(records),
                "notes_emitted": aggregate["notes_emitted"],
                "noop_discarded": aggregate["noop_discarded"],
                "failures": aggregate["failures"],
                "skipped_collision": aggregate["skipped_collision"],
                "skipped_idempotent": aggregate["skipped_idempotent"],
            },
            losses_aggregated=dict(losses),
            items=items,
            integrity_ok=aggregate["failures"] == 0,
        )
        self._write_manifest(manifest)
        return manifest

    # ------------------------------------------------------------- per-record

    def _process_record(
        self,
        record: dict,
        seq: int,
        mode: str,
        aggregate: Counter[str],
        losses: Counter[str],
    ) -> ImportItem:
        try:
            result = import_record(record, "claude-mem")
        except Exception as exc:  # noqa: BLE001 — fail-loud per item, batch continues
            _logger.error("importer.record_failed seq=%s: %s", seq, exc)
            aggregate["failures"] += 1
            source_id = str(record.get("id") or "") if isinstance(record, dict) else ""
            return ImportItem(
                seq=seq,
                source_record_id=source_id,
                vendor_operation="ADD",
                notes_emitted=[],
                loss_report={
                    "vendor": "claude-mem",
                    "source_record_id": source_id,
                    "fields_lost": [],
                    "fields_synthesized": [],
                    "structural_loss": [],
                    "notes": f"import_record raised: {exc}",
                },
                status=_STATUS_FAILED,
                error=str(exc),
            )

        loss = result["loss_report"]
        for key in loss.get("fields_lost", []):
            losses[f"fields_lost:{key}"] += 1
        for key in loss.get("fields_synthesized", []):
            losses[f"fields_synthesized:{key}"] += 1

        if not result["notes"]:
            aggregate["noop_discarded"] += 1
            return ImportItem(
                seq=seq,
                source_record_id=loss["source_record_id"],
                vendor_operation="ADD",
                notes_emitted=[],
                loss_report=loss,
                status=_STATUS_NOOP,
            )

        notes_emitted: list[dict[str, str]] = []
        for ep in result["notes"]:
            if mode == "dry-run":
                notes_emitted.append({"ep_id": ep.id, "action": "create"})
                aggregate["notes_emitted"] += 1
                continue
            status, error = self._ingest(ep, aggregate)
            notes_emitted.append({"ep_id": ep.id, "action": "create"})
            if error is not None:
                return ImportItem(
                    seq=seq,
                    source_record_id=loss["source_record_id"],
                    vendor_operation="ADD",
                    notes_emitted=notes_emitted,
                    loss_report=loss,
                    status=_STATUS_FAILED,
                    error=error,
                )
            if status == _STATUS_COLLISION:
                return ImportItem(
                    seq=seq,
                    source_record_id=loss["source_record_id"],
                    vendor_operation="ADD",
                    notes_emitted=notes_emitted,
                    loss_report=loss,
                    status=_STATUS_COLLISION,
                )
            if status == _STATUS_IDEMPOTENT:
                return ImportItem(
                    seq=seq,
                    source_record_id=loss["source_record_id"],
                    vendor_operation="ADD",
                    notes_emitted=notes_emitted,
                    loss_report=loss,
                    status=_STATUS_IDEMPOTENT,
                )

        return ImportItem(
            seq=seq,
            source_record_id=loss["source_record_id"],
            vendor_operation="ADD",
            notes_emitted=notes_emitted,
            loss_report=loss,
            status=_STATUS_COMMITTED,
        )

    def _ingest(self, ep: Any, aggregate: Counter[str]) -> tuple[str, str | None]:
        """Delegate one note to ``facade.remember`` and classify the result.

        Returns ``(status, error)`` where ``status`` is the manifest item status
        and ``error`` is non-None only on a hard failure. Collisions are
        RETURNED (``WriteResult.collisions_detected``), never raised.
        """
        by = cast(Provenance, dict(ep.provenance))
        by["session_id"] = f"claude-mem-import-{self._run_id}"
        payload = RememberPayload(
            body=ep.body or "",
            by=by,
            valid_at=ep.valid_at,
            cognitive_type=ep.cognitive_type,
            title=ep.title,
            summary=ep.summary,
        )
        try:
            result = self._facade.remember(payload, extraction_mode="skip")
        except Exception as exc:  # noqa: BLE001 — fail-loud per item, batch continues
            aggregate["failures"] += 1
            return _STATUS_FAILED, str(exc)

        if result.status == "NOOP":
            aggregate["skipped_idempotent"] += 1
            return _STATUS_IDEMPOTENT, None
        if result.status == "COLLISION":
            aggregate["skipped_collision"] += 1
            return _STATUS_COLLISION, None
        aggregate["notes_emitted"] += 1
        return _STATUS_COMMITTED, None

    # -------------------------------------------------------------- manifest

    def _write_manifest(self, manifest: ImportManifest) -> None:
        """Write the batch manifest to ``{output_dir}/{run_id}/manifest.json``."""
        if self._output_dir is None:
            return  # no output dir -> manifest is returned, not persisted
        run_dir = self._output_dir / self._run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "manifest.json"
        path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n")
        _logger.info("importer.manifest_written path=%s", path)


__all__ = ["ImportRunner"]
