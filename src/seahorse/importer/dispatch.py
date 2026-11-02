"""Importer vendor dispatch — the single public ``import_record`` entry point.

Each vendor module (``claude_mem``, ``chatgpt``) owns its own pure
``import_record``; this module routes to the right one by vendor string so the
runner and package surface stay vendor-agnostic. Adding a vendor means adding a
module and one registry line here — not editing the runner.
"""

from __future__ import annotations

from seahorse.importer.chatgpt import import_record as _import_chatgpt
from seahorse.importer.claude_mem import import_record as _import_claude_mem
from seahorse.importer.types import ImporterResult

_REGISTRY = {
    "claude-mem": _import_claude_mem,
    "chatgpt": _import_chatgpt,
}


def import_record(vendor_record: dict, vendor: str) -> ImporterResult:
    """Map one vendor record to F3.1 notes, routed by ``vendor``."""
    try:
        fn = _REGISTRY[vendor]
    except KeyError as exc:
        raise ValueError(f"unsupported vendor: {vendor!r}") from exc
    return fn(vendor_record, vendor)


__all__ = ["import_record"]
