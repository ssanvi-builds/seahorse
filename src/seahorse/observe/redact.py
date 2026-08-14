"""Deterministic secret redaction for the observer.

Runs at ENQUEUE time: nothing raw is ever persisted — the queue stores only
the already-redacted envelope. The redactor is a PURE function (same input →
same output → stable hash). The structural walk covers nested JSON (dict/list/str)
so secrets inside ``tool_input`` / ``tool_response`` are redacted, not just
top-level strings — the guarantee of strictly stronger redaction than claude-mem
only holds if structured fields and Read/Bash content are covered.

Patterns: bearer/API keys, ``.env`` lines, private keys PEM, userinfo in URLs,
known prefixes (``sk-``, ``AIza``, ``ghp_``, ``AKIA``, JWT).
"""

from __future__ import annotations

import re
from typing import Any

_REDACTED = "[REDACTED]"

# Bearer tokens: "Bearer <token>" — the scheme label survives for readability.
_BEARER_RE = re.compile(r"\b(Bearer)\s+[A-Za-z0-9._~+/=-]+")

# Known API key prefixes (OpenAI sk-, Google AIza, GitHub ghp_, AWS AKIA,
# Slack xox*). ``sk-`` is deliberately broad — it is almost always a secret.
_PREFIX_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]+"
    r"|AIza[A-Za-z0-9_-]{6,}"
    r"|ghp_[A-Za-z0-9]+"
    r"|AKIA[A-Z0-9]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]+"
    r")"
)

# JWT: three base64url segments, header always starts with ``eyJ`` (base64 of
# ``{"``). A strong heuristic — random base64 does not start with eyJ.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")

# PEM private keys (any key type, DOTALL so the body is covered). The BEGIN/END
# markers survive for readability; only the key material is redacted.
_PEM_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----).*?(-----END [A-Z ]*PRIVATE KEY-----)",
    re.DOTALL,
)

# Userinfo in URLs: ``scheme://user:pass@host`` → redact the userinfo, keep the
# scheme and the ``@`` so the URL shape survives.
_URL_USERINFO_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)([^/@\s]+)@")

# ``.env``-style assignment lines. The value is redacted ONLY when the key
# name suggests a secret (belt-and-braces for values that match no other
# pattern, e.g. ``PASSWORD=hunter2``).
_ENV_LINE_RE = re.compile(r"(?m)^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(\S+)(\s*)$")
_SECRET_KEY_HINT = re.compile(
    r"(?i)(key|token|secret|password|passwd|pass|api|auth|credential|pwd)"
)

# Application order: the most specific patterns first (PEM, JWT, URL userinfo,
# bearer, prefixes), then the env-line pass (which re-checks values already
# redacted — a no-op). Each pattern uses a function replacement that preserves
# the readable label (Bearer / PEM markers / URL scheme).


def _redact_bearer(match: re.Match[str]) -> str:
    return f"{match.group(1)} {_REDACTED}"


def _redact_pem(match: re.Match[str]) -> str:
    return f"{match.group(1)} {_REDACTED} {match.group(2)}"


def _redact_url_userinfo(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_REDACTED}@"


def _redact_env_line(match: re.Match[str]) -> str:
    indent, key, eq, value, trailing = match.groups()
    if _SECRET_KEY_HINT.search(key):
        return f"{indent}{key}{eq}{_REDACTED}{trailing}"
    return match.group(0)


def redact_text(text: str) -> str:
    """Redact known secret patterns from a text string. Pure."""
    out = _PEM_RE.sub(_redact_pem, text)
    out = _JWT_RE.sub(_REDACTED, out)
    out = _URL_USERINFO_RE.sub(_redact_url_userinfo, out)
    out = _BEARER_RE.sub(_redact_bearer, out)
    out = _PREFIX_RE.sub(_REDACTED, out)
    out = _ENV_LINE_RE.sub(_redact_env_line, out)
    return out


def redact_value(value: Any) -> Any:
    """Structural walk of a JSON value: redact every string, keep the shape.

    Immutable: returns a NEW object; the input is never mutated (project
    coding style). Scalars (int/float/bool/None) pass through untouched.
    """
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact an event payload structurally (the enqueue-time entry point)."""
    return redact_value(payload)  # type: ignore[return-value]


__all__ = ["redact_text", "redact_value", "redact_payload"]
