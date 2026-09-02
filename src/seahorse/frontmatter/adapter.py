"""Frontmatter adapter — parse/serialize/atomic-write over ``.md`` files.

Implements round-trip + serialization operationally. Two reconciliation
points where the implementation diverges from the spec's literal text to honour
the spec's STATED GOALS (code real > doc stale):

1. **Body separator.** The spec says the body includes its leading ``\\n`` inicial" and
   joins as ``---\\n{fm}\\n---{body}``. Taken literally, an engine-supplied body
   (``"# Madrid\\n"``, no leading newline) would render as ``---# Madrid`` —
   broken markdown. The adapter instead joins as ``---\\n{fm}\\n---\\n{body}``
   (explicit separator) and parses back with a closing delimiter of
   ``\\n---\\n`` (see ``RuamelRTHandler.split``): that delimiter consumes exactly
   ONE separator newline, so ``parse_file`` returns ``raw_body`` verbatim and the
   round-trip is byte-identical. A body that starts with a blank line keeps it
   (writing ``---\\n{fm}\\n---\\n\\n# Madrid\\n`` parses back to
   ``\\n# Madrid\\n``); a body with no leading newline stays that way. The
   engine's body and the file's body stay equal — no drift across writes.

2. **Merge preserves unchanged fields.** The spec wants comments, quote
   style, and key order preserved on fields the agent did not mutate. A naive
   merge that overwrites every schema field from the Pydantic dump would reset
   quote styles on every write. ``_merge_known`` instead compares each field's
   current baseline value to the incoming dump value in a canonical form and
   skips the field when they are equal — so unchanged fields keep their original
   ruamel object (quotes, comments, anchors) and only mutated fields are
   rewritten. ``x-*`` and Obsidian keys (``aliases``/``cssclasses``) are never in
   the dump, so they are always preserved.

Confined to ``seahorse/frontmatter/``: imports ``ruamel.yaml`` and ``frontmatter``
(the handler); neither leaks into the core.
"""

from __future__ import annotations

import contextlib
import copy
import io
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.errors import FrontmatterInvalid
from seahorse.frontmatter.handler import RuamelRTHandler, _make_yaml

_yaml = _make_yaml()


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def parse_file(
    path: Path, *, mvp: str = "0"
) -> tuple[CommentedMap, str, Episode]:
    """Dual-pass parse of the on-disk format. Returns ``(commented_map, body, ep)``.

    The body is byte-a-byte from the file: ``RuamelRTHandler.split`` keys on the
    closing delimiter ``\\n---\\n`` (which consumes exactly one separator
    newline), so ``raw_body`` is the body verbatim — no stripping. The commented
    map carries the original ruamel formatting (comments, quotes, order,
    ``x-*`` keys); the caller writes back onto it. ``ep`` is the validated
    ``Episode`` (without body — ``hydrate`` attaches it lazily).

    Raises ``FrontmatterInvalid`` on any validation failure (naive datetime,
    non-null ``expired_at`` in the first release). A file with no frontmatter returns an empty
    commented map and the whole file as body; ``model_validate`` then raises
    ``FrontmatterInvalid`` for the missing required fields — the migrator
    handles case A by building defaults rather than calling ``parse_file``.
    """
    text = path.read_text(encoding="utf-8")
    handler = RuamelRTHandler(_yaml)
    if handler.detect(text):
        try:
            fm, raw_body = handler.split(text)
            cm = handler.load(fm)
        except Exception as e:
            # A ruamel parse error (bad YAML syntax) is wrapped so every read
            # path surfaces the same typed error — with the source path —
            # instead of a raw ParserError that names no file (L7: an index
            # rebuild over one broken note reported an anonymous ParserError).
            raise FrontmatterInvalid(path, e) from e
    else:
        cm = CommentedMap()
        raw_body = text
    body = raw_body
    fm_plain = _to_plain(cm)
    try:
        ep = Episode.model_validate(fm_plain, context={"mvp": mvp})
    except ValidationError as e:
        raise FrontmatterInvalid(path, e) from e
    return cm, body, ep


def hydrate(path: Path, *, mvp: str = "0") -> Episode:
    """Parse + attach the body lazily."""
    _cm, body, ep = parse_file(path, mvp=mvp)
    return ep.model_copy(update={"body": body})


# ---------------------------------------------------------------------------
# serialize
# ---------------------------------------------------------------------------


def serialize(ep: Episode, path: Path, *, exclude_none: bool, mvp: str = "0") -> None:
    """Write ``ep`` to ``path``. Body is ``ep.body or ""``.

    ``mvp`` threads the validation phase to the baseline re-parse inside
    ``write_file`` (orthogonal to ``exclude_none``): callers in a later release
    (decay, a medium-term goal) pass ``mvp="1"`` so a baseline with a non-null
    ``expired_at`` does not trip the first-release read-path guard
    (``_expired_null_mvp0``) on the re-parse. Default ``"0"`` keeps existing
    callers and tests unchanged.
    """
    body = ep.body or ""
    write_file(path, ep, body, exclude_none=exclude_none, mvp=mvp)


def write_file(
    path: Path,
    ep: Episode,
    body: str,
    *,
    exclude_none: bool,
    baseline_cm: CommentedMap | None = None,
    mvp: str = "0",
) -> None:
    """Merge ``ep`` onto the baseline commented map and write atomically.

    Re-parses ``path`` for the baseline when ``baseline_cm`` is ``None`` and the
    file exists (existing files are always re-parsed) so ``x-*``, comments, and
    Obsidian fields survive. The re-parse uses ``mvp`` so a later-release
    baseline with a non-null ``expired_at`` does not raise
    ``FrontmatterInvalid`` on the guard ``_expired_null_mvp0``. When a caller
    passes ``baseline_cm`` explicitly (migrator fast path), it is deep-copied
    before merge so the caller-owned map is not mutated in place (immutability).
    ``exclude`` drops the model's ``__pydantic_extra__`` (``x-*`` keys the
    Episode carries) from the dump — they live in the baseline, not the dump.
    """
    if baseline_cm is None and path.exists():
        baseline_cm, _, _ = parse_file(path, mvp=mvp)
    cm = (
        copy.deepcopy(baseline_cm)
        if baseline_cm is not None
        else CommentedMap()
    )
    x_keys = set((ep.model_extra or {}).keys())
    dump = ep.model_dump(mode="json", exclude_none=exclude_none, exclude=x_keys)
    _merge_known(cm, dump)
    fm_text = _dump_yaml(cm)
    _atomic_write(path, f"---\n{fm_text}\n---\n{body}")


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

# The canonical Episode field order (declaration order of the Pydantic model).
# ``_merge_known`` walks the dump in this order so new keys land canonically.
_FIELD_ORDER = tuple(Episode.model_fields.keys())


def _merge_known(cm: CommentedMap, dump: dict[str, Any]) -> None:
    """Write dump's schema fields onto ``cm`` preserving everything else.

    For each dump field in canonical order: if absent from ``cm``, append; if
    present and the canonical value differs, update; if present and equal,
    skip (preserves the baseline's ruamel object — quotes, comments, anchors).
    Recurses into nested mappings (``provenance``). Never touches ``x-*`` or
    non-schema keys (they are not in the dump).
    """
    ordered = sorted(
        dump.items(), key=lambda kv: _field_index(kv[0], len(dump))
    )
    for key, value in ordered:
        if key not in cm:
            cm[key] = _to_ruamel(value)
            continue
        current = cm[key]
        if isinstance(value, dict) and isinstance(current, (CommentedMap, dict)):
            # ``current`` is normally a CommentedMap (parsed baseline); the plain
            # ``dict`` arm is defensive. When mutating in place (CommentedMap) the
            # change persists via the alias; when converting a plain dict we must
            # write the merged result back, or the recursion's work is discarded.
            if isinstance(current, CommentedMap):
                _merge_known(current, value)
            else:
                nested = _to_ruamel(current)
                _merge_known(nested, value)
                cm[key] = nested
            continue
        if _canonical(value) == _canonical(current):
            continue
        cm[key] = _to_ruamel(value)


def _field_index(key: str, fallback: int) -> int:
    try:
        return _FIELD_ORDER.index(key)
    except ValueError:
        # Unknown keys (should not happen — dump only has schema fields) sort
        # after the known ones, preserving their relative order.
        return len(_FIELD_ORDER) + fallback


# ---------------------------------------------------------------------------
# conversion helpers
# ---------------------------------------------------------------------------


def _to_plain(node: Any) -> Any:
    """Recursively unwrap ruamel containers into plain dict/list, keep scalars.

    ``CommentedMap``/``CommentedSeq`` are dict/list subclasses, so this is a
    shallow copy of structure with scalars (including ruamel ``TimeStamp``, a
    ``datetime`` subclass) passed through unchanged — Pydantic accepts them.
    """
    if isinstance(node, dict):
        return {str(k): _to_plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_to_plain(v) for v in node]
    return node


def _to_ruamel(value: Any) -> Any:
    """Convert a plain (json-mode dump) value to a ruamel container for insertion."""
    if isinstance(value, dict):
        cm = CommentedMap()
        for k, v in value.items():
            cm[k] = _to_ruamel(v)
        return cm
    if isinstance(value, list):
        cs = CommentedSeq()
        for v in value:
            cs.append(_to_ruamel(v))
        return cs
    return value


def _canonical(value: Any) -> Any:
    """A comparable form of a field value for the merge skip-check.

    Datetimes canonicalize to ISO-8601 UTC ``Z`` (matching the Episode ``_z``
    serializer, so a parsed ``TimeStamp`` and the dump's string compare equal).
    Containers canonicalize to tuples/frozensets so order-independent dicts
    compare equal.
    """
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return tuple(sorted((str(k), _canonical(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_canonical(v) for v in value)
    return value


def _dump_yaml(cm: CommentedMap) -> str:
    """Dump the commented map to a YAML string (no trailing newline)."""
    buf = io.StringIO()
    _yaml.dump(cm, buf)
    return buf.getvalue().rstrip("\n")


def _atomic_write(path: Path, text: str) -> None:
    """Atomic write: tmp file in the same directory, then ``os.replace``.

    ``os.replace`` is atomic on the same filesystem, so a watcher (Obsidian)
    never sees a half-written file. The tmp lives next to the target so the
    rename never crosses filesystems. On any failure (disk full, permission,
    encoding error, KeyboardInterrupt) the tmp is best-effort unlinked so a
    stale tmp never litters the vault — the original file is untouched because
    the rename has not happened.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise