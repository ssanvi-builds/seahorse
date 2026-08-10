"""Tests for ``seahorse.observe.redact`` — deterministic secret redaction.

The redactor is a PURE function (same input → same output → stable hash,
obsiforge §4.4). It runs at ENQUEUE time: nothing raw is ever persisted — the
queue stores only the already-redacted envelope. The structural walk covers
nested JSON (dict/list/str) so secrets inside ``tool_input`` / ``tool_response``
are redacted, not just top-level strings (obsiforge §15.2 redesign 3).

The claim "strictly stronger than claude-mem" (whose ``pending_messages``
stores ``tool_input``/``tool_response`` raw) is pinned by the frozen fixtures
below: real secret shapes must come out redacted.
"""

from __future__ import annotations

from seahorse.observe.redact import (
    redact_payload,
    redact_text,
    redact_value,
)

# ---------------------------------------------------------------------------
# redact_text — known secret patterns
# ---------------------------------------------------------------------------


def test_redact_bearer_token() -> None:
    out = redact_text("Authorization: Bearer sk-abc123def456")
    assert "sk-abc123def456" not in out
    assert "Bearer" in out  # the scheme label survives


def test_redact_bearer_token_standalone() -> None:
    out = redact_text("token=Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out


def test_redact_api_key_prefixes() -> None:
    for secret in ("sk-abc123", "AIzaSyD-abc123", "ghp_abc123", "AKIAABC123", "xoxb-1234"):
        out = redact_text(f"key={secret}")
        assert secret not in out, f"{secret!r} leaked"


def test_redact_jwt() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop"
    out = redact_text(f"token {jwt}")
    assert jwt not in out


def test_redact_env_line() -> None:
    out = redact_text("OPENAI_API_KEY=sk-abc123\nDATABASE_URL=postgres://u:p@host/db")
    assert "sk-abc123" not in out
    assert "postgres://u:p@host/db" not in out


def test_redact_private_key_pem() -> None:
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n"
        "-----END PRIVATE KEY-----"
    )
    out = redact_text(pem)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ" not in out
    assert "BEGIN PRIVATE KEY" in out  # the marker survives for readability


def test_redact_userinfo_in_url() -> None:
    out = redact_text("https://user:supersecret@example.com/api")
    assert "supersecret" not in out
    assert "user:supersecret" not in out
    # The scheme + host survive; only the userinfo is redacted.
    assert "https://[REDACTED]@example.com/api" in out


def test_redact_plain_text_unchanged() -> None:
    text = "The quick brown fox jumps over the lazy dog."
    assert redact_text(text) == text


def test_redact_is_deterministic() -> None:
    text = "Authorization: Bearer sk-abc123\nkey=ghp_xyz"
    assert redact_text(text) == redact_text(text)


# ---------------------------------------------------------------------------
# redact_value — structural walk of JSON
# ---------------------------------------------------------------------------


def test_redact_value_nested_dict() -> None:
    value = {
        "headers": {"Authorization": "Bearer sk-abc123"},
        "body": {"nested": {"api_key": "AIzaSyD-xyz"}},
    }
    out = redact_value(value)
    assert "sk-abc123" not in str(out)
    assert "AIzaSyD-xyz" not in str(out)
    assert out["headers"]["Authorization"] != "Bearer sk-abc123"


def test_redact_value_list() -> None:
    value = ["Bearer sk-abc123", {"k": "ghp_xyz"}, 42, None]
    out = redact_value(value)
    assert "sk-abc123" not in str(out)
    assert "ghp_xyz" not in str(out)
    assert out[2] == 42
    assert out[3] is None


def test_redact_value_scalars_passthrough() -> None:
    assert redact_value(42) == 42
    assert redact_value(None) is None
    assert redact_value(True) is True
    assert redact_value(3.14) == 3.14


def test_redact_value_does_not_mutate_input() -> None:
    value = {"a": "Bearer sk-abc123"}
    redact_value(value)
    assert value == {"a": "Bearer sk-abc123"}  # immutable pattern


# ---------------------------------------------------------------------------
# redact_payload — the enqueue-time entry point
# ---------------------------------------------------------------------------


def test_redact_payload_tool_input_and_response() -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": "curl -H 'Authorization: Bearer sk-abc123' https://api.example.com",
        "tool_response": '{"api_key": "AIzaSyD-xyz", "ok": true}',
    }
    out = redact_payload(payload)
    assert "sk-abc123" not in str(out)
    assert "AIzaSyD-xyz" not in str(out)
    assert out["tool_name"] == "Bash"  # non-secret fields survive


def test_redact_payload_is_deterministic() -> None:
    payload = {"tool_input": "Bearer sk-abc123", "n": 1}
    assert redact_payload(payload) == redact_payload(payload)


def test_redact_payload_empty() -> None:
    assert redact_payload({}) == {}
