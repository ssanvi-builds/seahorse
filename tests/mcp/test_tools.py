"""Tests for the 7 MCP tool handlers (the MCP server) — delegation purity +
guards-before-read.

Uses ``RecordingFacade`` to assert WHAT facade method was called, with WHICH
kwargs, and that wire-shape guards fire BEFORE any facade read (call counts
stay zero on the error path). This is the structural enforcement outcome-only
tests cannot provide (the structural-review lesson).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from seahorse.disclosure.types import PITPoint
from seahorse.facade.errors import PitRecallNotSupportedMVP0, SeahorseError
from seahorse.facade.types import RememberPayload
from seahorse.mcp.tools import dispatch, handle_build_pit
from tests.mcp.conftest import RecordingFacade, make_episode, make_pit


def _by() -> dict:
    return {"agent_id": "a", "session_id": "s", "source_type": "agent"}


_CANONICAL_BODY = (
    "## Trigger\n\nT\n\n## Steps\n\nS\n\n## Validation\n\nV\n\n## Rationale\n\nR"
)


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

    def test_tags_rejected_at_wire(self) -> None:
        # tags are not advertised this release (the facade refuses them), so
        # the wire rejects them like any unknown field.
        facade = RecordingFacade()
        resp = dispatch("remember", {"body": "hi", "by": _by(), "tags": ["a", "b"]}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.remember_calls) == 0

    def test_summary_forwarded(self) -> None:
        # The wire accepts summary as an additive editorial field.
        facade = RecordingFacade()
        dispatch("remember", {"body": "hi", "by": _by(), "summary": "A summary"}, facade, 1)
        assert facade.remember_calls[0]["payload"].summary == "A summary"

    def test_summary_absent_is_none(self) -> None:
        facade = RecordingFacade()
        dispatch("remember", {"body": "hi", "by": _by()}, facade, 1)
        assert facade.remember_calls[0]["payload"].summary is None

    def test_summary_too_long_rejected_at_wire(self) -> None:
        from seahorse.disclosure.types import SUMMARY_MAX_CHARS

        facade = RecordingFacade()
        resp = dispatch(
            "remember",
            {"body": "hi", "by": _by(), "summary": "y" * (SUMMARY_MAX_CHARS + 1)},
            facade,
            1,
        )
        assert resp["error"]["code"] == -32602
        assert len(facade.remember_calls) == 0

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

    def test_clock_not_overridden(self) -> None:
        # Mirror of forget's now=None invariant: remember never overrides the
        # clock (the wire has no now); the facade owns it.
        facade = RecordingFacade()
        dispatch("remember", {"body": "hi", "by": _by()}, facade, 1)
        assert facade.remember_calls[0]["now"] is None

    def test_rejects_now_at_wire(self) -> None:
        # `now` is NOT in the schema → additionalProperties: false rejects it.
        facade = RecordingFacade()
        resp = dispatch(
            "remember",
            {"body": "hi", "by": _by(), "now": "2020-01-01T00:00:00Z"},
            facade,
            1,
        )
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
        # k absent → handler does NOT pass k (would clobber facade default).
        # Structural: the key is ABSENT from the recording, not collapsed to None.
        assert "k" not in facade.recall_calls[0]

    def test_k_forwarded_when_present(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall", {"query": "x", "k": 5}, facade, 1)
        assert facade.recall_calls[0]["k"] == 5

    def test_cognitive_type_forwarded_when_present(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall", {"query": "x", "cognitive_type": "semantic"}, facade, 1)
        assert facade.recall_calls[0]["cognitive_type"] == "semantic"

    def test_cognitive_type_absent_when_missing(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall", {"query": "x"}, facade, 1)
        assert "cognitive_type" not in facade.recall_calls[0]

    def test_subject_filter_forwarded_when_present(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall", {"query": "x", "subject_filter": "Sergio"}, facade, 1)
        assert facade.recall_calls[0]["subject_filter"] == "Sergio"

    def test_subject_filter_absent_when_missing(self) -> None:
        facade = RecordingFacade()
        facade.build_pit_result = None
        dispatch("recall", {"query": "x"}, facade, 1)
        assert "subject_filter" not in facade.recall_calls[0]

    def test_pit_recall_surfaces_mvp0_refusal(self) -> None:
        # A resolved pit → recall raises PitRecallNotSupportedMVP0 (the
        # later-release path).
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

    def test_rejects_unknown_axis_at_wire(self) -> None:
        facade = RecordingFacade()
        resp = dispatch(
            "recall_timeline", {"anchor_ep_id": "ep-1", "axis": "procedure"}, facade, 1
        )
        assert resp["error"]["code"] == -32602
        assert len(facade.recall_timeline_calls) == 0

    def test_resolves_loose_pit_via_build_pit(self) -> None:
        # Structural: recall_timeline receives the RESOLVED PITPoint (not raw
        # pit_kind) when the caller sends the loose pit_kind + pit_t pair.
        facade = RecordingFacade()
        facade.build_pit_result = make_pit("state_at")
        dispatch(
            "recall_timeline",
            {"anchor_ep_id": "ep-1", "pit_kind": "state_at", "pit_t": "2026-07-16T12:00:00Z"},
            facade,
            1,
        )
        pit = facade.recall_timeline_calls[0]["pit"]
        assert isinstance(pit, PITPoint)
        assert pit.kind == "state_at"


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

    def test_resolves_loose_pit_via_build_pit(self) -> None:
        # Structural: recall_full receives the RESOLVED PITPoint when the caller
        # sends the loose pit_kind + pit_t pair (mirrors recall's path).
        facade = RecordingFacade()
        facade.build_pit_result = make_pit("state_at")
        dispatch(
            "recall_full",
            {"ep_ids": ["ep-1"], "pit_kind": "state_at", "pit_t": "2026-07-16T12:00:00Z"},
            facade,
            1,
        )
        pit = facade.recall_full_calls[0]["pit"]
        assert isinstance(pit, PITPoint)
        assert pit.kind == "state_at"

    def test_resolved_pit_surfaces_pit_full_not_supported(self) -> None:
        # A resolved pit → recall_full raises PitFullNotSupported (the
        # disclosure shaper's first-release refusal). The MCP server translates
        # it to -32050 with exception_class, no synthetic seahorse_code.
        from seahorse.disclosure.types import PitFullNotSupported

        facade = RecordingFacade()
        facade.build_pit_result = make_pit("state_at")
        facade.recall_full_raise = PitFullNotSupported()
        resp = dispatch(
            "recall_full",
            {"ep_ids": ["ep-1"], "pit_kind": "state_at", "pit_t": "2026-07-16T12:00:00Z"},
            facade,
            1,
        )
        assert resp["error"]["code"] == -32050
        assert resp["error"]["data"]["exception_class"] == "PitFullNotSupported"
        assert "seahorse_code" not in resp["error"]["data"]


# ---------------------------------------------------------------------------
# improve
# ---------------------------------------------------------------------------


class TestImproveHandler:
    def test_delegates_verbatim_by(self) -> None:
        # The MCP server does NOT synthesize effective provenance — the facade
        # does.
        facade = RecordingFacade()
        by = {"agent_id": "a", "session_id": "s", "source_type": "human"}
        dispatch("improve", {"ep_id": "ep-1", "new_body": "new", "by": by}, facade, 1)
        call = facade.improve_calls[0]
        assert call["ep_id"] == "ep-1"
        assert call["new_body"] == "new"
        assert call["by"] == by  # verbatim, no effective-provenance synthesis
        assert "extraction_mode" not in call["by"]  # the MCP server did not add it
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
        # The wire has no `now`; the MCP server never overrides the clock.
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
        # facade.build_pit resolves (pit wins). The MCP server does not pick.
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
        # The MCP server does NOT pre-validate; facade.build_pit raises
        # E_PIT_REQUIRES_T.
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

    @pytest.mark.parametrize(
        "tool,args,record_attr",
        [
            ("remember", {"body": "hi", "by": _by()}, "remember_calls"),
            ("recall", {"query": "x"}, "recall_calls"),
            ("recall_timeline", {"anchor_ep_id": "ep-1"}, "recall_timeline_calls"),
            ("recall_full", {"ep_ids": ["ep-1"]}, "recall_full_calls"),
            ("improve", {"ep_id": "ep-1", "new_body": "new", "by": _by()}, "improve_calls"),
            ("forget", {"ep_id": "ep-1", "reason": "wrong", "by": _by()}, "forget_calls"),
            ("build_pit", {}, "build_pit_calls"),
            ("skill_add", {"body": _CANONICAL_BODY, "by": _by()}, "remember_calls"),
            ("skill_show", {"ep_id": "ep-1"}, "recall_full_calls"),
            ("skill_list", {}, "get_vigente_calls"),
            ("skill_search", {"query": "x"}, "recall_calls"),
            ("freshness_view", {"ep_id": "ep-1"}, "freshness_calls"),
            ("audit_log", {"ep_id": "ep-1"}, "audit_calls"),
            ("follow_supersedes_chain", {"ep_id": "ep-1"}, "chain_calls"),
        ],
    )
    def test_all_handlers_direct_callable(self, tool, args, record_attr) -> None:
        # Every handler is callable directly (not just via dispatch) and
        # produces a non-error tools/call envelope + records the facade call.
        from seahorse.mcp.tools import TOOL_HANDLERS

        facade = RecordingFacade()
        facade.build_pit_result = None
        resp = TOOL_HANDLERS[tool](facade, dict(args), 1)
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"]["isError"] is False
        assert len(getattr(facade, record_attr)) == 1

# ---------------------------------------------------------------------------
# skill_add / skill_show + deferred read-only tools.
# ---------------------------------------------------------------------------


class TestSkillAddHandler:
    def test_delegates_record_procedure_skip(self) -> None:
        facade = RecordingFacade()
        body = "## Trigger\n\nT\n\n## Steps\n\nS\n\n## Validation\n\nV\n\n## Rationale\n\nR"
        resp = dispatch("skill_add", {"body": body, "by": _by()}, facade, 1)
        assert resp["result"]["isError"] is False
        assert len(facade.remember_calls) == 1
        call = facade.remember_calls[0]
        assert call["extraction_mode"] == "skip"
        assert call["payload"].cognitive_type == "procedural"
        assert call["payload"].body == body

    def test_forwards_x_metadata(self) -> None:
        facade = RecordingFacade()
        body = "## Trigger\n\nT\n\n## Steps\n\nS\n\n## Validation\n\nV\n\n## Rationale\n\nR"
        dispatch(
            "skill_add",
            {"body": body, "by": _by(), "trigger": "user asks X", "version": "1.0"},
            facade,
            1,
        )
        prov = facade.remember_calls[0]["payload"].by
        assert prov["x-seahorse-skill-trigger"] == "user asks X"
        assert prov["x-seahorse-skill-version"] == "1.0"


class TestSkillShowHandler:
    def test_gates_high_trust_as_instruction(self) -> None:
        facade = RecordingFacade()
        ep = make_episode(
            source_type="human", provenance={"source_type": "human", "agent_id": "sergio"}
        )
        facade.full_result = [make_full_detail_for(ep)]
        resp = dispatch("skill_show", {"ep_id": "ep-1"}, facade, 1)
        out = json.loads(resp["result"]["content"][0]["text"])
        assert out["trust"] == "high"
        assert out["as_instruction"] is True

    def test_gates_low_trust_as_citation(self) -> None:
        facade = RecordingFacade()
        ep = make_episode(
            source_type="importer", provenance={"source_type": "importer", "importer_vendor": "x"}
        )
        facade.full_result = [make_full_detail_for(ep)]
        resp = dispatch("skill_show", {"ep_id": "ep-1"}, facade, 1)
        out = json.loads(resp["result"]["content"][0]["text"])
        assert out["trust"] == "low"
        assert out["as_instruction"] is False

    def test_min_trust_high_gates_medium(self) -> None:
        facade = RecordingFacade()
        ep = make_episode(
            source_type="agent", provenance={"source_type": "agent", "extraction_mode": "skip"}
        )
        facade.full_result = [make_full_detail_for(ep)]
        resp = dispatch(
            "skill_show", {"ep_id": "ep-1", "min_trust": "high"}, facade, 1
        )
        out = json.loads(resp["result"]["content"][0]["text"])
        assert out["as_instruction"] is False


class TestSkillListHandler:
    def test_delegates_get_vigente_filters_procedural_sorted_desc(self) -> None:
        facade = RecordingFacade()
        facade.vigente_result = [
            make_episode(
                "ep-old",
                cognitive_type="procedural",
                created_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            ),
            make_episode(
                "ep-new",
                cognitive_type="procedural",
                created_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
            ),
            make_episode("ep-semantic", cognitive_type="semantic"),
        ]
        resp = dispatch("skill_list", {}, facade, 1)
        assert resp["result"]["isError"] is False
        assert len(facade.get_vigente_calls) == 1
        out = json.loads(resp["result"]["content"][0]["text"])
        # Only procedural, newest first, projected to the CLI shape.
        assert [e["ep_id"] for e in out] == ["ep-new", "ep-old"]
        assert set(out[0]) == {"ep_id", "subject", "summary", "created_at"}

    def test_truncates_to_top_k(self) -> None:
        facade = RecordingFacade()
        facade.vigente_result = [
            make_episode(
                f"ep-{i}",
                cognitive_type="procedural",
                created_at=datetime(2026, 7, i + 1, 12, 0, tzinfo=UTC),
            )
            for i in range(5)
        ]
        resp = dispatch("skill_list", {"top_k": 2}, facade, 1)
        out = json.loads(resp["result"]["content"][0]["text"])
        assert len(out) == 2

    def test_wire_rejects_top_k_zero_before_any_read(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("skill_list", {"top_k": 0}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.get_vigente_calls) == 0


class TestSkillSearchHandler:
    def test_delegates_recall_with_procedural_filter(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("skill_search", {"query": "how to"}, facade, 1)
        assert resp["result"]["isError"] is False
        assert len(facade.recall_calls) == 1
        call = facade.recall_calls[0]
        assert call["query"] == "how to"
        assert call["cognitive_type"] == "procedural"
        # k is NOT forwarded when top_k absent (facade default TOP_K applies).
        assert "k" not in call

    def test_forwards_k_only_when_top_k_present(self) -> None:
        facade = RecordingFacade()
        dispatch("skill_search", {"query": "how to", "top_k": 5}, facade, 1)
        assert facade.recall_calls[0]["k"] == 5

    def test_wire_rejects_empty_query_before_any_read(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("skill_search", {"query": ""}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert len(facade.recall_calls) == 0


class TestReadOnlyTools:
    def test_freshness_view_delegates(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("freshness_view", {"ep_id": "ep-1"}, facade, 1)
        assert resp["result"]["isError"] is False
        assert facade.freshness_calls[0]["ep_id"] == "ep-1"
        out = json.loads(resp["result"]["content"][0]["text"])
        assert out["fact_id"] == "fact-1"
        assert out["stale"] is True

    def test_audit_log_delegates(self) -> None:
        facade = RecordingFacade()
        _ = dispatch("audit_log", {"ep_id": "ep-1"}, facade, 1)
        assert facade.audit_calls[0]["ep_id"] == "ep-1"

    def test_follow_supersedes_chain_delegates(self) -> None:
        facade = RecordingFacade()
        _ = dispatch("follow_supersedes_chain", {"ep_id": "ep-1"}, facade, 1)
        assert facade.chain_calls[0]["ep_id"] == "ep-1"

    def test_missing_ep_id_rejected_before_read(self) -> None:
        facade = RecordingFacade()
        resp = dispatch("freshness_view", {}, facade, 1)
        assert resp["error"]["code"] == -32602
        assert facade.freshness_calls == []


def make_full_detail_for(ep):
    """Build a FullDetail from an Episode (for skill_show tests)."""
    from seahorse.contracts.engine import FreshnessView
    from seahorse.disclosure.types import EpisodeProvenance, FullDetail

    return FullDetail(
        episode=ep,
        provenance=EpisodeProvenance(
            agent_id=(ep.provenance or {}).get("agent_id"),
            session_id=(ep.provenance or {}).get("session_id"),
            source_type=ep.source_type,
            extraction_mode=(ep.provenance or {}).get("extraction_mode"),
            model_used=None,
        ),
        freshness=FreshnessView(
            fact_id=ep.fact_id, age_days=0, stale=False, pending_ingest=False, regime="agent"
        ),
        pit=None,
    )
