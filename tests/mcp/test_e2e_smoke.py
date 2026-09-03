"""End-to-end stdio smoke for the MCP server against the REAL engine +
disclosure shaper + persistence + write path stack.

Drives the stdio JSON-RPC loop in-process (``io.StringIO`` for stdin/stdout)
against a real ``MemoryFacade`` built by the conftest ``real_facade`` fixture.
Lifecycle: initialize → tools/list (14 tools) → remember → recall (shows it) →
improve → recall (new present, old gone) → forget → recall (gone) →
notification (no response) → EOF (loop ends).
"""

from __future__ import annotations

import io
import json

from seahorse.mcp.profile import serve


def _line(resp: dict, idx: int) -> dict:
    """Pull the idx-th response dict from the stdout buffer."""
    lines = resp.getvalue().splitlines()
    return json.loads(lines[idx])


def _responses(resp) -> list[dict]:
    return [json.loads(line) for line in resp.getvalue().splitlines() if line.strip()]


class TestStdioProtocol:
    def test_initialize_and_tools_list(self, real_facade) -> None:
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
        )
        stdout = io.StringIO()
        serve(real_facade, stdin=stdin, stdout=stdout)
        resps = _responses(stdout)
        assert len(resps) == 2
        assert resps[0]["result"]["protocolVersion"] == "2025-11-25"
        names = {t["name"] for t in resps[1]["result"]["tools"]}
        assert len(names) == 14
        assert "remember" in names

    def test_notification_produces_no_response(self, real_facade) -> None:
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        stdout = io.StringIO()
        serve(real_facade, stdin=stdin, stdout=stdout)
        resps = _responses(stdout)
        assert len(resps) == 1  # only initialize; the notification got no reply

    def test_malformed_json_returns_parse_error(self, real_facade) -> None:
        stdin = io.StringIO("not json\n")
        stdout = io.StringIO()
        serve(real_facade, stdin=stdin, stdout=stdout)
        resps = _responses(stdout)
        assert resps[0]["error"]["code"] == -32700

    def test_empty_lines_ignored(self, real_facade) -> None:
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        stdin = io.StringIO("\n  \n" + line + "\n")
        stdout = io.StringIO()
        serve(real_facade, stdin=stdin, stdout=stdout)
        resps = _responses(stdout)
        assert len(resps) == 1
        assert len(resps[0]["result"]["tools"]) == 14

    def test_eof_ends_loop_cleanly(self, real_facade) -> None:
        # No exception; serve returns when stdin is exhausted.
        stdin = io.StringIO("")
        stdout = io.StringIO()
        serve(real_facade, stdin=stdin, stdout=stdout)
        assert stdout.getvalue() == ""


class TestFullLifecycle:
    def _run(self, facade, lines: list[str]) -> list[dict]:
        stdin = io.StringIO("".join(line + "\n" for line in lines))
        stdout = io.StringIO()
        serve(facade, stdin=stdin, stdout=stdout)
        return _responses(stdout)

    def _content(self, resp: dict) -> dict:
        return json.loads(resp["result"]["content"][0]["text"])

    def test_remember_recall_improve_forget(self, real_facade) -> None:
        remember_args = {
            "body": "Sergio lives in Madrid",
            "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
        }
        # remember → returns ACTIVE; we extract ep_id via recall (INDEX shows it)
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "remember", "arguments": remember_args}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "recall", "arguments": {"query": "madrid"}}}),
        ]
        resps = self._run(real_facade, lines)
        wr = self._content(resps[0])
        assert wr["status"] == "ACTIVE"
        old_id = wr["ep_id"]
        assert old_id is not None

        rows = self._content(resps[1])
        ids = [r["ep_id"] for r in rows]
        assert old_id in ids  # recall shows the remembered episode

        # improve → new Episode superseding the old
        improve_args = {
            "ep_id": old_id,
            "new_body": "Sergio lives in Barcelona",
            "by": {"agent_id": "sergio", "session_id": "s2", "source_type": "human"},
            "reason": "correction",
        }
        resps2 = self._run(
            real_facade,
            [
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "improve", "arguments": improve_args}}),
                json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                            "params": {"name": "recall", "arguments": {"query": "barcelona"}}}),
            ],
        )
        new_ep = self._content(resps2[0])
        assert new_ep["id"] != old_id
        assert new_ep["supersedes"] == old_id

        rows_after = self._content(resps2[1])
        ids_after = [r["ep_id"] for r in rows_after]
        assert new_ep["id"] in ids_after
        assert old_id not in ids_after  # old no longer the current version

        # forget the new episode → invalidated
        forget_args = {
            "ep_id": new_ep["id"],
            "reason": "wrong",
            "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
        }
        resps3 = self._run(
            real_facade,
            [
                json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                            "params": {"name": "forget", "arguments": forget_args}}),
                json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                            "params": {"name": "recall", "arguments": {"query": "anything"}}}),
            ],
        )
        forgotten = self._content(resps3[0])
        assert forgotten["invalid_at"] is not None

        rows_final = self._content(resps3[1])
        ids_final = [r["ep_id"] for r in rows_final]
        assert new_ep["id"] not in ids_final
        assert old_id not in ids_final

    def test_build_pit_returns_null_for_all_none(self, real_facade) -> None:
        resps = self._run(
            real_facade,
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "build_pit", "arguments": {}}}),
            ],
        )
        assert self._content(resps[0]) is None

    def test_remember_with_tags_rejected_at_wire(self, real_facade) -> None:
        # tags are NOT advertised (the facade refuses them this release), so
        # additionalProperties: false rejects them at the wire — the facade is
        # never touched on a wire-shape failure.
        resps = self._run(
            real_facade,
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "remember", "arguments": {
                                "body": "hi",
                                "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                                "tags": ["x"],
                            }}}),
            ],
        )
        assert resps[0]["error"]["code"] == -32602

    def test_full_lifecycle_single_session(self, real_facade) -> None:
        # ONE stdin stream, ONE serve() call: initialize → tools/list →
        # remember → recall → build_pit. Proves the stdio loop stays coherent
        # across a mixed-method client session (handshake + list + two calls +
        # a third call) and returns the full ordered response stream in order.
        # The improve/forget arc needs the remembered ep_id, which the engine
        # generates at write time — that substitution is exercised by
        # test_remember_recall_improve_forget across _run passes instead.
        remember_args = {
            "body": "Sergio lives in Madrid",
            "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
        }
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "remember", "arguments": remember_args}}),
            json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "recall", "arguments": {"query": "madrid"}}}),
            json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "build_pit", "arguments": {}}}),
        ]
        stdin = io.StringIO("".join(line + "\n" for line in lines))
        stdout = io.StringIO()
        serve(real_facade, stdin=stdin, stdout=stdout)
        resps = _responses(stdout)
        assert len(resps) == 5
        # ordered: handshake → list → remember → recall → build_pit
        assert resps[0]["id"] == 1
        assert resps[0]["result"]["protocolVersion"] == "2025-11-25"
        assert len(resps[1]["result"]["tools"]) == 14
        wr = self._content(resps[2])
        assert wr["status"] == "ACTIVE"
        old_id = wr["ep_id"]
        assert old_id in [r["ep_id"] for r in self._content(resps[3])]
        assert self._content(resps[4]) is None  # build_pit all-None → null

    def test_recall_timeline_and_full_real_stack(self, real_facade) -> None:
        # recall_timeline + recall_full against the REAL disclosure shaper (the
        # two tools the original e2e smoke did not exercise). Asserts the
        # TimelineWindow and FullDetail wire shapes come back canonicalized.
        remember_args = {
            "body": "Sergio lives in Madrid",
            "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
        }
        resps = self._run(
            real_facade,
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "remember", "arguments": remember_args}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "recall", "arguments": {"query": "madrid"}}}),
            ],
        )
        old_id = self._content(resps[0])["ep_id"]

        # recall_timeline on the anchor → TimelineWindow with entries
        tl = self._run(
            real_facade,
            [
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "recall_timeline",
                                       "arguments": {"anchor_ep_id": old_id}}}),
            ],
        )
        window = self._content(tl[0])
        assert window["anchor_ep_id"] == old_id
        assert window["axis"] == "supersedes_chain"
        assert isinstance(window["entries"], list)
        assert any(e["ep_id"] == old_id for e in window["entries"])

        # recall_full on the ep_id → list[FullDetail] with the hydrated episode
        full = self._run(
            real_facade,
            [
                json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                            "params": {"name": "recall_full",
                                       "arguments": {"ep_ids": [old_id]}}}),
            ],
        )
        details = self._content(full[0])
        assert isinstance(details, list)
        assert details[0]["episode"]["id"] == old_id
        assert details[0]["episode"]["body"] == "Sergio lives in Madrid"