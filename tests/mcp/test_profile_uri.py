"""Tests for the profile URI + server composition + JSON-RPC method dispatch (#13)."""

from __future__ import annotations

import re

from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import RememberPayload
from seahorse.mcp import profile
from seahorse.mcp.profile import (
    _PROFILE_RE,
    _PROTOCOL_VERSION,
    PROFILE_URI,
    build_server,
    handle_request,
)


class TestProfileURI:
    def test_uri_matches_regex(self) -> None:
        assert _PROFILE_RE.match(PROFILE_URI)

    def test_uri_is_io_seahorse_memory_v1(self) -> None:
        assert PROFILE_URI == "io.seahorse.memory/v1"

    def test_typo_io_sehrose_rejected_by_regex(self) -> None:
        # the startup assert (module import) would have failed otherwise; the
        # regex is the guard, so prove it rejects the canonical typo.
        assert not _PROFILE_RE.match("io.sehrose.memory/v1")

    def test_uri_requires_version_suffix(self) -> None:
        assert not _PROFILE_RE.match("io.seahorse.memory")

    def test_protocol_version_pinned(self) -> None:
        assert _PROTOCOL_VERSION == "2025-11-25"


class TestBuildServer:
    def test_returns_memory_facade(self, tmp_path) -> None:
        facade = build_server(tmp_path / "server.db")
        assert isinstance(facade, MemoryFacade)

    def test_round_trip_remember_recall(self, tmp_path) -> None:
        facade = build_server(tmp_path / "server.db")
        r = facade.remember(
            RememberPayload(
                body="Sergio lives in Madrid",
                by={"source_type": "agent", "agent_id": "a", "session_id": "s"},
            )
        )
        assert r.status == "ACTIVE"
        rows = facade.recall("madrid")
        assert r.ep_id in [row.ep_id for row in rows]


class TestInitializeAndList:
    def test_initialize_response(self) -> None:
        resp = handle_request(None, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2025-11-25"
        assert "tools" in resp["result"]["capabilities"]
        assert resp["result"]["serverInfo"]["name"] == "seahorse-memory"

    def test_tools_list_returns_seven(self) -> None:
        # tools/list does not touch the facade — handle_request needs one only for calls
        resp = handle_request(None, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        assert names == {
            "remember",
            "recall",
            "recall_timeline",
            "recall_full",
            "improve",
            "forget",
            "build_pit",
        }

    def test_each_tool_has_input_schema_with_defs(self) -> None:
        resp = handle_request(None, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        for tool in resp["result"]["tools"]:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["$defs"] is not None
            assert tool["inputSchema"]["additionalProperties"] is False


class TestNotifications:
    def test_initialized_notification_no_response(self) -> None:
        # notifications have no id → no response
        resp = handle_request(
            None,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert resp is None

    def test_unknown_notification_no_response(self) -> None:
        resp = handle_request(None, {"jsonrpc": "2.0", "method": "notifications/cancelled"})
        assert resp is None


class TestMethodErrors:
    def test_unknown_method(self) -> None:
        resp = handle_request(None, {"jsonrpc": "2.0", "id": 9, "method": "bogus"})
        assert resp["error"]["code"] == -32601

    def test_missing_method(self) -> None:
        resp = handle_request(None, {"jsonrpc": "2.0", "id": 9})
        assert resp["error"]["code"] == -32600

    def test_non_object_request(self) -> None:
        resp = handle_request(None, "not a dict")
        assert resp["error"]["code"] == -32600
        assert resp["id"] is None


class TestToolsCallDispatch:
    def _facade(self, tmp_path) -> None:
        # helper kept for tools/call tests that need a real facade
        return build_server(tmp_path / "call.db")

    def test_call_unknown_tool(self, tmp_path) -> None:
        facade = self._facade(tmp_path)
        resp = handle_request(
            facade,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "bogus", "arguments": {}},
            },
        )
        assert resp["error"]["code"] == -32601

    def test_call_remember_against_real_stack(self, tmp_path) -> None:
        facade = self._facade(tmp_path)
        resp = handle_request(
            facade,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "remember",
                    "arguments": {
                        "body": "Sergio lives in Madrid",
                        "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    },
                },
            },
        )
        assert resp["result"]["isError"] is False
        import json

        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["status"] == "ACTIVE"

    def test_call_remember_wire_shape_error(self, tmp_path) -> None:
        facade = self._facade(tmp_path)
        resp = handle_request(
            facade,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "remember",
                    "arguments": {"body": "x" * 100_000, "by": {}},
                },
            },
        )
        assert resp["error"]["code"] == -32602
        assert resp["error"]["data"]["wire_shape_error"] is True

    def test_call_missing_name(self, tmp_path) -> None:
        facade = self._facade(tmp_path)
        resp = handle_request(
            facade,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"arguments": {}}},
        )
        assert resp["error"]["code"] == -32602


def test_profile_module_assertion_held() -> None:
    # If the module imported, the regex assert on PROFILE_URI already held.
    # This test documents that and guards a future URI regression.
    assert re.match(r"^io\.seahorse\.memory/v[0-9]+$", profile.PROFILE_URI)