"""Tests for the 7 MCP tool handlers (#13) — delegation purity + guards-before-read.

Uses ``RecordingFacade`` to assert WHAT facade method was called, with WHICH
kwargs, and that wire-shape guards fire BEFORE any facade read (call counts
stay zero on the error path). This is the structural enforcement outcome-only
tests cannot provide (the #8 adversarial-review lesson).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from seahorse.disclosure.types import PITPoint
from seahorse.facade.errors import PitRecallNotSupportedMVP0, SeahorseError
from seahorse.facade.types import RememberPayload
from seahorse.mcp.tools import dispatch, handle_build_pit
from tests.mcp.conftest import RecordingFacade, make_pit


def _by() -> dict:
    return {"agent_id": "a", "session_id": "s", "source_type": "agent"}


# ---------------------------------------------------------------------------
# remember
# ---------------------------------------------------------------------------


class TestRememberHandler:
    def test_delegates_payload_and_modes(self) -> None:
        facade = RecordingFacade()
        args = {"body": "hello", "by": _by(), "skip_extraction": True}
        resp = dispatch("remember", args, facade, 1)
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"]["isError"] is False
        assert len(facade.remember_calls) == 1
        call = facade.remember_calls[0]
        assert isinstance(call["payload"], RememberPayload)
        assert call["payload"].body == "hello"
        assert call["skip_extraction"] is True
        assert call["extraction_mode"] is None

    def test_passes_extraction_mode(self) -> None:
        facade = RecordingFacade()
        dispatch("remember", {"body": "hi", "by": _by(), "extraction_mode": "llm"}, facade, 1)
        assert facade.remember_calls[0]["extraction_mode"] == "llm"
        assert facade.remember_calls[0]["skip_extraction"] is None

    def test_does_not_forward_subject(self) -> None:
        # subject is NOT on the wire (additionalProperties: false rejects it);
        # RememberPayload has no subject field — the engine derives it from body.
        facade = RecordingFacade()
        resp = dispatch("remember", {"body": "hi", "by": _by(), "subject": "Sergio"}, facade, 1)
        assert resp["error"]["code"] == -32602  # wire rejected
        assert len(facade.remember_calls) == 0

    def test_does_not_forward_supersedes(self) -> None:
        facade = RecordingFacade()
        resp = dispatch(
            "remember", {"body": "hi", "by": _by(), "supersedes": "ep-1"}, facade, 1
        )
        assert resp["error"]["code"] == -32602
        assert len(facade.remember_calls) == 0

    def test_tags_forwarded_as_tuple(self) -> None:
        facade = RecordingFacade()
        dispatch("remember", {"body": "hi", "by": _by(), "tags": ["a", "b"]}, facade, 1)
        assert facade.remember_calls[0]["payload"].tags == ("a", "b")

    def test_wire_shape_error_fires_before_facade(self) -> None:
        # body too long → validate raises → facade.remember NEVER called.
        facade = RecordingFacade()
        resp = dispatch("remember", {"body": "x" * 100_000, "by": _by()}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert resp["error"]["data"]["wire_shape_error"] is True
        assert len(facade.remember_calls) == 0  # guard fired before any read

    def test_missing_by_fires_before_facade(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("remember", {"body": "hi"}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.remember_calls) == 0


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


class TestRecallHandler:
    def test_delegates_query_and_resolved_pit(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None  # no PIT
        dispatch("recall", {"query": "madrid"}, facade, 1)
        assert facade.recall_calls[0]["query"] == "madrid"
        assert facade.recall_calls[0]["pit"] is None
        # build_pit was called once (resolve step)
        assert len(facade.build_pit_calls) == 1

    def test_resolves_loose_pit_via_build_pit(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = make_pit("state_at")
        dispatch(
            "recall",
            {"query": "x", "pit_kind": "state_at", "pit_t": "2026-07-16T12:00:00Z"},
            facade,
            1,
        )
        # the handler passed the raw (pit=None, pit_kind, t) to facade.build_pit
        assert facade.build_pit_calls[0]["pit"] is None
        assert facade.build_pit_calls[0]["pit_kind"] == "state_at"
        # recall received the RESOLVED pit (the returned PITPoint), not pit_kind
        assert isinstance(facade.recall_calls[0]["pit"], PITPoint)
        assert facade.recall_calls[0]["pit"].kind == "state_at"

    def test_does_not_pass_anchor_ep_id(self) -> None:
        # anchor_ep_id is rejected at wire-shape; it can never reach recall.
        facade = RecordingFacade()
        resp = dispatch("recall", {"query": "x", "anchor_ep_id": "ep-1"}, facade, 1)
        assert resp["error"]["code"] == -32602
        # rejected before the facade — recall was never called
        assert len(facade.recall_calls) == 0

    def test_does_not_pass_hops(self) -> None:
        facade = RecordingFacade()
        dispatch("recall", {"query": "x", "hops": 2}, facade, 1)
        assert len(facade.recall_calls) == 0  # rejected at wire

    def test_k_only_forwarded_when_present(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall", {"query": "x"}, facade, 1)
        # k absent → handler does NOT pass k=None (would clobber facade default)
        assert facade.recall_calls[0]["k"] is None

    def test_k_forwarded_when_present(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall", {"query": "x", "k": 5}, facade, 1)
        assert facade.recall_calls[0]["k"] == 5

    def test_pit_recall_surfaces_mvp0_refusal(self) -> None:
        # A resolved pit → recall raises PitRecallNotSupportedMVP0 (MVP-1 path).
        facade = RecordingFacade()
        facade.recall_raise = PitRecallNotSupportedMVP0()
        facade.build_pit_result = make_pit("state_at")
        resp = dispatch(
            "recall",
            {"query": "x", "pit_kind": "state_at", "pit_t": "2026-07-16T12:00:00Z"},
            facade,
            1,
        )
        assert resp["error"]["code"] == -32007  # E_PIT_RECALL_MVP_0
        assert resp["error"]["data"]["seahorse_code"] == "E_PIT_RECALL_MVP_0"

    def test_empty_query_rejected_at_wire(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("recall", {"query": ""}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.recall_calls) == 0


# ---------------------------------------------------------------------------
# recall_timeline / recall_full
# ---------------------------------------------------------------------------


class TestRecallTimelineHandler:
    def test_delegates_anchor_axis_pit(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall_timeline", {"anchor_ep_id": "ep-1", "axis": "fact_id_scope"}, facade, 1)
        call = facade.recall_timeline_calls[0]
        assert call["anchor"] == "ep-1"
        assert call["axis"] == "fact_id_scope"
        assert call["pit"] is None

    def test_axis_defaults_to_supersedes_chain(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall_timeline", {"anchor_ep_id": "ep-1"}, facade, 1)
        assert facade.recall_timeline_calls[0]["axis"] == "supersedes_chain"

    def test_rejects_mvp1_axis_at_wire(self) -> None:
        facade = RecordingFacade()
        resp = dispatch(
            "recall_timeline", {"anchor_ep_id": "ep-1", "axis": "created_at"}, facade, 1
        )
        assert resp["error"]["code"] == -32602
        assert len(facade.recall_timeline_calls) == 0


class TestRecallFullHandler:
    def test_delegates_ep_ids_and_pit(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall_full", {"ep_ids": ["ep-1", "ep-2"]}, facade, 1)
        assert facade.recall_full_calls[0]["ep_ids"] == ["ep-1", "ep-2"]
        assert facade.recall_full_calls[0]["pit"] is None

    def test_empty_batch_rejected_at_wire(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("recall_full", {"ep_ids": []}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.recall_full_calls) == 0

    def test_oversized_batch_rejected_at_wire(self) -> None:
        from seahorse.disclosure.types import MAX_FULL_BATCH

        facade = RecordingFacade()
        resp = dispatch("recall_full", {"ep_ids": ["e"] * (MAX_FULL_BATCH + 1)}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.recall_full_calls) == 0


# ---------------------------------------------------------------------------
# improve
# ---------------------------------------------------------------------------


class TestImproveHandler:
    def test_delegates_verbatim_by(self) -> None:
        # #13 does NOT synthesize effective provenance — the facade does.
        facade = RecordingFacade()
        by = {"agent_id": "a", "session_id": "s", "source_type": "human"}
        dispatch("improve", {"ep_id": "ep-1", "new_body": "new", "by": by}, facade, 1)
        call = facade.improve_calls[0]
        assert call["ep_id"] == "ep-1"
        assert call["new_body"] == "new"
        assert call["by"] == by  # verbatim, no effective-provenance synthesis
        assert "extraction_mode" not in call["by"]  # #13 did not add it
        assert call["reason"] == "correction"  # default

    def test_passes_reason_when_present(self) -> None:
        facade = RecordingFacade()
        dispatch(
            "improve",
            {"ep_id": "ep-1", "new_body": "new", "by": _by(), "reason": "contradiction"},
            facade,
            1,
        )
        assert facade.improve_calls[0]["reason"] == "contradiction"

    def test_passes_valid_at_parsed(self) -> None:
        facade = RecordingFacade()
        dispatch(
            "improve",
            {"ep_id": "ep-1", "new_body": "new", "by": _by(), "valid_at": "2026-07-16T12:00:00Z"},
            facade,
            1,
        )
        assert facade.improve_calls[0]["valid_at"] == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_wire_shape_error_before_facade(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("improve", {"ep_id": "ep-1", "new_body": "", "by": _by()}, facade, 1)
        # new_body minLength 1 → wire rejects (facade never sees it)
        assert resp["error"]["code"] == -32602
        assert len(facade.improve_calls) == 0


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


class TestForgetHandler:
    def test_delegates_with_now_none(self) -> None:
        # OQ #13 DECIDIDA: the wire has no `now`; #13 never overrides the clock.
        facade = RecordingFacade()
        dispatch("forget", {"ep_id": "ep-1", "reason": "wrong", "by": _by()}, facade, 1)
        call = facade.forget_calls[0]
        assert call["ep_id"] == "ep-1"
        assert call["reason"] == "wrong"
        assert call["now"] is None  # NOT a caller-provided value

    def test_rejects_now_at_wire(self) -> None:
        # `now` is NOT in the schema → additionalProperties: false rejects it.
        facade = RecordingFacade()
        resp = dispatch(
            "forget",
            {"ep_id": "ep-1", "reason": "wrong", "by": _by(), "now": "2020-01-01T00:00:00Z"},
            facade,
            1,
        )
        assert resp["error"]["code"] == -32602
        assert len(facade.forget_calls) == 0

    def test_missing_reason_rejected_at_wire(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("forget", {"ep_id": "ep-1", "by": _by()}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.forget_calls) == 0


# ---------------------------------------------------------------------------
# build_pit
# ---------------------------------------------------------------------------


class TestBuildPitHandler:
    def test_all_none_returns_none(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        resp = dispatch("build_pit", {}, facade, 1)
        assert json.loads(resp["result"]["content"][0]["text"]) is None
        assert facade.build_pit_calls[0] == {"pit": None, "pit_kind": None, "t": None}

    def test_pit_object_wins(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = make_pit("state_at")
        dispatch(
            "build_pit",
            {
                "pit": {"kind": "state_at", "t": "2026-07-16T12:00:00Z"},
                "pit_kind": "known_at",
                "t": "2026-07-16T13:00:00Z",
            },
            facade,
            1,
        )
        # precedence is the facade's: pit object + (pit_kind, t) all passed;
        # facade.build_pit resolves (pit wins). #13 does not pick.
        call = facade.build_pit_calls[0]
        assert call["pit"] is not None  # parsed PITPoint
        assert call["pit_kind"] == "known_at"
        assert call["t"] == datetime(2026, 7, 16, 13, 0, 0, tzinfo=UTC)

    def test_loose_pair_uses_t_not_pit_t(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = make_pit("known_at")
        dispatch(
            "build_pit",
            {"pit_kind": "known_at", "t": "2026-07-16T12:00:00Z"},
            facade,
            1,
        )
        assert facade.build_pit_calls[0]["t"] == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_pit_kind_without_t_propagates_facade_error(self) -> None:
        # #13 does NOT pre-validate; facade.build_pit raises E_PIT_REQUIRES_T.
        facade = RecordingFacade()
        facade.build_pit_raise = SeahorseError(code="E_PIT_REQUIRES_T", detail="needs t")
        resp = dispatch("build_pit", {"pit_kind": "state_at"}, facade, 1)
        assert resp["error"]["data"]["seahorse_code"] == "E_PIT_REQUIRES_T"

    def test_bad_pit_kind_rejected_at_wire(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("build_pit", {"pit_kind": "bogus"}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.build_pit_calls) == 0


# ---------------------------------------------------------------------------
# dispatch — unknown tool + exception translation
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_unknown_tool_returns_32601(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("bogus", {}, facade, 1)
        assert resp["error"]["code"] == -32601
        assert resp["error"]["data"]["unknown_tool"] == "bogus"

    def test_translates_cat_a_error(self) -> None:
        facade = RecordingFacade()
        facade.remember_raise = SeahorseError(code="E_EMPTY_BODY", detail="x")
        resp = dispatch("remember", {"body": "hi", "by": _by()}, facade, 1)
        assert resp["error"]["code"] == -32001
        assert resp["error"]["data"]["seahorse_code"] == "E_EMPTY_BODY"

    def test_translates_generic_exception(self) -> None:
        facade = RecordingFacade()
        facade.remember_raise = RuntimeError("boom")
        resp = dispatch("remember", {"body": "hi", "by": _by()}, facade, 1)
        assert resp["error"]["code"] == -32603
        assert resp["error"]["data"]["exception_class"] == "RuntimeError"

    def test_success_result_is_envelope(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("remember", {"body": "hi", "by": _by()}, facade, "req-1")
        assert resp["id"] == "req-1"
        assert resp["result"]["isError"] is False
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["status"] == "ACTIVE"
        assert payload["collisions_detected"] == []


class TestHandlerDirectCall:
    """Handlers are callable directly (not just via dispatch)."""

    def test_handle_build_pit_direct(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = make_pit("state_at")
        resp = handle_build_pit(facade, {"pit_kind": "state_at", "t": "2026-07-16T12:00:00Z"}, 1)
        text = resp["result"]["content"][0]["text"]
        out = json.loads(text)
        assert out["kind"] == "state_at"
        assert out["t"] == "2026-07-16T12:00:00Z"