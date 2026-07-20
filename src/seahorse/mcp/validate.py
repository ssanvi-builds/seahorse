"""Minimal JSON Schema validator (subset) for the MCP profile (#13).

#13 owns wire-shape validation (R2 f5-13: wire-shape only; #12 owns semantic).
This validates the tool ``arguments`` against the tool's ``inputSchema`` so
caps, enums, required fields, ``additionalProperties: false``, and the
``date-time`` format are enforced BEFORE the facade is touched (a wire-shape
failure raises ``WireShapeError`` → JSON-RPC ``-32602`` with no ``seahorse_code``
— the request never reached #12).

Subset supported (enough for the 7 tool schemas in wire_schema.py):
- ``type``: a string OR a list of strings (union, e.g. ``["string","null"]``)
- ``enum``: value must be in the list (``null`` is a valid enum member)
- ``const``
- ``required`` (object): each listed property present
- ``properties`` (object): recurse
- ``additionalProperties: false`` (object): reject keys not in ``properties``
- ``minLength`` / ``maxLength`` (string)
- ``minimum`` / ``maximum`` (number/integer)
- ``minItems`` / ``maxItems`` (array)
- ``format: "date-time"`` (string): parseable ISO-8601 with timezone
- ``$ref: "#/$defs/Name"``: resolve against ``defs``
- ``oneOf`` / ``anyOf``: at least one (oneOf: exactly one) schema matches
- ``items`` (array): recurse

Anything else is ignored (fail-OPEN on unknown keywords, but the schemas do
not rely on unsupported keywords). This is a hand-rolled validator, NOT a full
JSON Schema implementation — kept small and explicit on purpose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from seahorse.mcp.errors import WireShapeError

_DATE_TIME = "date-time"


def _resolve(ref: str, defs: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/$defs/"):
        raise WireShapeError(f"unsupported $ref {ref!r}")
    name = ref.removeprefix("#/$defs/")
    if name not in defs:
        raise WireShapeError(f"unresolved $ref {ref!r}")
    return defs[name]


def _type_matches(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        # bool is a subclass of int — exclude it explicitly.
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return False


def _check_type(value: Any, schema: Any, defs: dict[str, Any], path: str) -> None:
    t = schema["type"]
    types = t if isinstance(t, list) else [t]
    if not any(_type_matches(value, tt) for tt in types):
        raise WireShapeError(
            f"expected type {types}, got {type(value).__name__}", field=path
        )


def _check_enum(value: Any, schema: Any, path: str) -> None:
    if value not in schema["enum"]:
        raise WireShapeError(
            f"value {value!r} not in enum {schema['enum']!r}", field=path
        )


def _check_format(value: Any, schema: Any, path: str) -> None:
    if not isinstance(value, str):
        return  # type check handles non-string
    fmt = schema.get("format")
    if fmt == _DATE_TIME:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WireShapeError(f"invalid date-time {value!r}: {exc}", field=path) from exc
        if dt.tzinfo is None:
            raise WireShapeError(
                f"date-time {value!r} must include a timezone", field=path
            )


def _matches(value: Any, schema: Any, defs: dict[str, Any], path: str) -> bool:
    """Return True if ``value`` validates against ``schema`` (no raise)."""
    try:
        _validate_schema(value, schema, defs, path)
    except WireShapeError:
        return False
    return True


def _validate_schema(
    value: Any, schema: Any, defs: dict[str, Any], path: str
) -> None:
    if not isinstance(schema, dict):
        raise WireShapeError(f"schema must be an object at {path}")

    # $ref takes precedence (the referenced schema is the authority).
    if "$ref" in schema:
        _validate_schema(value, _resolve(schema["$ref"], defs), defs, path)
        return

    # oneOf / anyOf — these are constraints alongside the other keywords (JSON
    # Schema ANDs all keywords), so we do NOT early-return: the subs are
    # checked for the of-condition, then the remaining keywords still apply.
    if "oneOf" in schema:
        matches = sum(
            1 for sub in schema["oneOf"] if _matches(value, sub, defs, path)
        )
        if matches != 1:
            raise WireShapeError(
                f"oneOf: expected exactly one match, got {matches}", field=path
            )
    if "anyOf" in schema and not any(
        _matches(value, sub, defs, path) for sub in schema["anyOf"]
    ):
        raise WireShapeError("anyOf: no schema matched", field=path)

    if "const" in schema and value != schema["const"]:
        raise WireShapeError(f"expected const {schema['const']!r}", field=path)

    if "type" in schema:
        _check_type(value, schema, defs, path)

    if "enum" in schema:
        _check_enum(value, schema, path)

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise WireShapeError(
                f"length {len(value)} < minLength {schema['minLength']}", field=path
            )
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise WireShapeError(
                f"length {len(value)} > maxLength {schema['maxLength']}", field=path
            )
        if "format" in schema:
            _check_format(value, schema, path)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise WireShapeError(f"{value} < minimum {schema['minimum']}", field=path)
        if "maximum" in schema and value > schema["maximum"]:
            raise WireShapeError(f"{value} > maximum {schema['maximum']}", field=path)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise WireShapeError(
                f"items {len(value)} < minItems {schema['minItems']}", field=path
            )
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise WireShapeError(
                f"items {len(value)} > maxItems {schema['maxItems']}", field=path
            )
        if "items" in schema:
            for i, item in enumerate(value):
                _validate_schema(item, schema["items"], defs, f"{path}[{i}]")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        if "required" in schema:
            for req in schema["required"]:
                if req not in value:
                    raise WireShapeError(f"missing required field {req!r}", field=path)
        if schema.get("additionalProperties") is False:
            # When ``properties`` is empty/absent, every key is extra — that is
            # the correct JSON Schema semantics for ``{"additionalProperties":
            # false}`` with no declared properties (any non-empty object is
            # invalid). The guard must NOT short-circuit on an empty props dict.
            extra = set(value) - set(props)
            if extra:
                raise WireShapeError(
                    f"unknown fields {sorted(extra)!r} (additionalProperties: false)",
                    field=path,
                )
        for key, subschema in props.items():
            if key in value:
                _validate_schema(value[key], subschema, defs, f"{path}.{key}")


def validate(value: Any, schema: dict[str, Any], *, defs: dict[str, Any] | None = None) -> None:
    """Validate ``value`` against ``schema``. Raise ``WireShapeError`` on failure.

    ``defs`` is the ``$defs`` map used to resolve ``$ref``. If omitted, the
    schema's own ``$defs`` key is used.
    """
    resolved_defs = defs if defs is not None else schema.get("$defs", {})
    _validate_schema(value, schema, resolved_defs, "<root>")


__all__ = ["validate"]