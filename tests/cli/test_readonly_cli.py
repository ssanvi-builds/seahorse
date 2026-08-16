"""``seahorse freshness-view`` / ``audit-log`` / ``follow-supersedes-chain``.

The three read-only facade tools, now exposed on the CLI (parity with the MCP
server which already had them). Unit tests assert delegation purity (the run_*
function calls the right facade method with the right args); e2e tests drive
the real stack through the invoke harness.
"""

from __future__ import annotations

import io
import json

from seahorse.cli.primitives import (
    run_audit_log,
    run_follow_supersedes_chain,
    run_freshness_view,
)
from tests.cli.builders import RecordingFacade, make_audit_event, make_episode
from tests.cli.conftest import invoke


def _out() -> io.StringIO:
    return io.StringIO()


class TestFreshnessView:
    def test_delegates_to_facade(self, recording: RecordingFacade) -> None:
        run_freshness_view(recording, ep_id="ep-1", fmt="human", out=_out())
        assert len(recording.freshness_calls) == 1
        assert recording.freshness_calls[0]["ep_id"] == "ep-1"

    def test_renders_json(self, recording: RecordingFacade) -> None:
        out = _out()
        run_freshness_view(recording, ep_id="ep-1", fmt="json", out=out)
        obj = json.loads(out.getvalue())
        assert obj["fact_id"] == "fact-1"
        assert obj["stale"] is True


class TestAuditLog:
    def test_delegates_to_facade(self, recording: RecordingFacade) -> None:
        recording.audit_result = [make_audit_event()]
        run_audit_log(recording, ep_id="ep-1", fmt="human", out=_out())
        assert len(recording.audit_calls) == 1
        assert recording.audit_calls[0]["ep_id"] == "ep-1"

    def test_renders_jsonl(self, recording: RecordingFacade) -> None:
        recording.audit_result = [make_audit_event(), make_audit_event(primitive="forget")]
        out = _out()
        run_audit_log(recording, ep_id="ep-1", fmt="jsonl", out=out)
        lines = out.getvalue().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["primitive"] == "apply"


class TestFollowSupersedesChain:
    def test_delegates_to_facade(self, recording: RecordingFacade) -> None:
        recording.chain_result = [make_episode("ep-1")]
        run_follow_supersedes_chain(recording, ep_id="ep-1", fmt="human", out=_out())
        assert len(recording.chain_calls) == 1
        assert recording.chain_calls[0]["ep_id"] == "ep-1"

    def test_renders_json(self, recording: RecordingFacade) -> None:
        recording.chain_result = [make_episode("ep-1")]
        out = _out()
        run_follow_supersedes_chain(recording, ep_id="ep-1", fmt="json", out=out)
        assert json.loads(out.getvalue())[0]["id"] == "ep-1"


class TestReadOnlyE2E:
    def test_freshness_view_round_trip(self, vault) -> None:
        _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "Sergio lives in Madrid"])
        ep_id = json.loads(out)["ep_id"]
        code, out, err = invoke(["--vault", str(vault), "freshness-view", ep_id])
        assert code == 0, err
        assert "Freshness" in out

    def test_audit_log_round_trip(self, vault) -> None:
        _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "Sergio lives in Madrid"])
        ep_id = json.loads(out)["ep_id"]
        code, out, err = invoke(["--vault", str(vault), "audit-log", ep_id])
        assert code == 0, err
        assert "Audit log" in out

    def test_follow_supersedes_chain_round_trip(self, vault) -> None:
        _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "Sergio lives in Madrid"])
        ep_id = json.loads(out)["ep_id"]
        _, out, _ = invoke(
            ["--vault", str(vault), "--json", "improve", ep_id, "Sergio lives in Barcelona"]
        )
        code, out, err = invoke(
            ["--vault", str(vault), "follow-supersedes-chain", ep_id]
        )
        assert code == 0, err
        assert "Supersedes chain" in out
