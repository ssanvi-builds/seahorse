"""``seahorse.cli.primitives`` — delegation purity + CLI-shape guards.

The ``RecordingFacade`` double structurally enforces #14's invariants:
- WHAT facade method was called, with WHICH kwargs, in WHICH order.
- CLI-border guards (caps, vocabulary, datetime parse) fire BEFORE any facade
  call — call counts stay zero on the usage-error path.
- ``run_expire_revalidate`` never reaches the facade (SO-14-05, Cat C exit 75).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from seahorse.cli.errors import CliNotInMVP0, CliUsageError
from seahorse.cli.primitives import (
    run_expire_revalidate,
    run_forget,
    run_improve,
    run_recall,
    run_recall_full,
    run_recall_timeline,
    run_remember,
)
from seahorse.constants import (
    BODY_MAX_CHARS,
    EP_ID_MAX_CHARS,
    PROVENANCE_ID_MAX_CHARS,
    QUERY_MAX_CHARS,
    REASON_MAX_CHARS,
)
from seahorse.facade.types import RememberPayload
from tests.cli.builders import RecordingFacade, make_index_row


def _out() -> io.StringIO:
    return io.StringIO()


# ---------------------------------------------------------------------------
# remember — delegation.
# ---------------------------------------------------------------------------


def test_remember_delegates_payload_and_mode(recording: RecordingFacade):
    run_remember(
        recording, body="hello world", source_type="agent",
        agent_id="a1", session_id="s1", extraction_mode="skip",
        fmt="human", out=_out(),
    )
    assert len(recording.remember_calls) == 1
    call = recording.remember_calls[0]
    payload: RememberPayload = call["payload"]
    assert payload.body == "hello world"
    assert payload.by == {"source_type": "agent", "agent_id": "a1", "session_id": "s1"}
    assert call["extraction_mode"] == "skip"
    assert call["skip_extraction"] is None
    assert call["now"] is None


def test_remember_valid_at_parsed_to_datetime(recording: RecordingFacade):
    run_remember(
        recording, body="x", valid_at="2026-07-16T12:00:00Z",
        fmt="human", out=_out(),
    )
    payload = recording.remember_calls[0]["payload"]
    assert payload.valid_at == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_remember_body_cap_fires_before_facade(recording: RecordingFacade):
    """CLI cap guard fires BEFORE any facade call (call count stays 0)."""
    with pytest.raises(CliUsageError, match="body"):
        run_remember(recording, body="x" * (BODY_MAX_CHARS + 1), fmt="human", out=_out())
    assert recording.remember_calls == []  # guard fired first


def test_remember_bad_source_type_fires_before_facade(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="source-type"):
        run_remember(recording, body="x", source_type="alien", fmt="human", out=_out())
    assert recording.remember_calls == []


def test_remember_bad_cognitive_type_fires_before_facade(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="cognitive-type"):
        run_remember(recording, body="x", cognitive_type="nope", fmt="human", out=_out())
    assert recording.remember_calls == []


def test_remember_agent_id_cap_fires(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="agent-id"):
        run_remember(
            recording, body="x", agent_id="a" * (PROVENANCE_ID_MAX_CHARS + 1),
            fmt="human", out=_out(),
        )
    assert recording.remember_calls == []


def test_remember_bad_valid_at_raises_usage(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="valid-at"):
        run_remember(recording, body="x", valid_at="not-a-date", fmt="human", out=_out())
    assert recording.remember_calls == []


def test_remember_does_not_validate_mode_cli(recording: RecordingFacade):
    """extraction_mode validation is DEFERRED to the facade (delegation purity).

    The CLI does NOT raise ``CliUsageError`` for a bogus mode — it forwards the
    value verbatim and lets the facade raise ``E_INVALID_EXTRACTION_MODE`` (66).
    With the RecordingFacade (which does not validate), the bogus mode is simply
    recorded as-forwarded; the real facade path is covered in ``test_app``.
    """
    run_remember(
        recording, body="x", extraction_mode="llm_partial", fmt="human", out=_out()
    )
    # The facade WAS called with the bogus mode forwarded verbatim (no CLI guard).
    assert len(recording.remember_calls) == 1
    assert recording.remember_calls[0]["extraction_mode"] == "llm_partial"


def test_remember_json_output_shape(recording: RecordingFacade):
    o = _out()
    run_remember(recording, body="x", fmt="json", out=o)
    import json
    obj = json.loads(o.getvalue())
    assert obj["ep_id"] == "ep-1"
    assert obj["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# recall — delegation + forwarded-only-when-present.
# ---------------------------------------------------------------------------


def test_recall_delegates_query_and_kwargs(recording: RecordingFacade):
    recording.recall_result = [make_index_row("ep-9")]
    run_recall(
        recording, query="madrid", top_k=5, cognitive_type="semantic",
        subject_filter="Sergio", fmt="human", out=_out(),
    )
    assert len(recording.recall_calls) == 1
    call = recording.recall_calls[0]
    assert call["query"] == "madrid"
    assert call["k"] == 5
    assert call["cognitive_type"] == "semantic"
    assert call["subject_filter"] == "Sergio"
    assert call["pit"] is None


def test_recall_absent_kwargs_are_absent_in_recording(recording: RecordingFacade):
    """No --cognitive-type / --subject-filter → those keys are ABSENT, not None."""
    run_recall(recording, query="x", fmt="human", out=_out())
    call = recording.recall_calls[0]
    assert "cognitive_type" not in call
    assert "subject_filter" not in call
    assert call["pit"] is None  # pit IS always forwarded (explicit)


def test_recall_query_cap_fires_before_facade(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="query"):
        run_recall(recording, query="x" * (QUERY_MAX_CHARS + 1), fmt="human", out=_out())
    assert recording.recall_calls == []


def test_recall_builds_pit_when_kind_given(recording: RecordingFacade):
    recording.build_pit_result = None  # build_pit returns None is fine for INDEX
    run_recall(
        recording, query="x", pit_kind="state_at",
        pit_t="2026-07-16T12:00:00Z", fmt="human", out=_out(),
    )
    assert len(recording.build_pit_calls) == 1
    assert recording.build_pit_calls[0]["pit_kind"] == "state_at"
    assert recording.build_pit_calls[0]["t"] == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_recall_no_pit_kind_skips_build_pit(recording: RecordingFacade):
    run_recall(recording, query="x", fmt="human", out=_out())
    assert recording.build_pit_calls == []


def test_recall_bad_pit_t_raises_usage(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="pit-t"):
        run_recall(
            recording, query="x", pit_kind="state_at", pit_t="bad",
            fmt="human", out=_out(),
        )
    assert recording.recall_calls == []


# ---------------------------------------------------------------------------
# recall-timeline / recall-full.
# ---------------------------------------------------------------------------


def test_recall_timeline_delegates(recording: RecordingFacade):
    run_recall_timeline(
        recording, anchor_ep_id="ep-1", axis="supersedes_chain",
        fmt="human", out=_out(),
    )
    call = recording.recall_timeline_calls[0]
    assert call["anchor"] == "ep-1"
    assert call["axis"] == "supersedes_chain"
    assert call["pit"] is None


def test_recall_timeline_anchor_cap_fires(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="anchor-ep-id"):
        run_recall_timeline(
            recording, anchor_ep_id="e" * (EP_ID_MAX_CHARS + 1), fmt="human", out=_out()
        )
    assert recording.recall_timeline_calls == []


def test_recall_full_delegates_ep_ids(recording: RecordingFacade):
    run_recall_full(recording, ep_ids=["ep-1", "ep-2"], fmt="human", out=_out())
    call = recording.recall_full_calls[0]
    assert call["ep_ids"] == ["ep-1", "ep-2"]
    assert call["pit"] is None


def test_recall_full_pit_forwarded(recording: RecordingFacade):
    recording.build_pit_result = None
    run_recall_full(
        recording, ep_ids=["ep-1"], pit_kind="known_at",
        pit_t="2026-07-16T12:00:00Z", fmt="human", out=_out(),
    )
    assert recording.recall_full_calls[0]["pit"] is None  # build_pit returned None
    assert recording.build_pit_calls[0]["pit_kind"] == "known_at"


def test_recall_full_ep_id_cap_fires(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="ep-id"):
        run_recall_full(
            recording, ep_ids=["e" * (EP_ID_MAX_CHARS + 1)], fmt="human", out=_out()
        )
    assert recording.recall_full_calls == []


# ---------------------------------------------------------------------------
# improve / forget.
# ---------------------------------------------------------------------------


def test_improve_delegates(recording: RecordingFacade):
    run_improve(
        recording, ep_id="ep-1", new_body="corrected", reason="fix",
        source_type="human", fmt="human", out=_out(),
    )
    call = recording.improve_calls[0]
    assert call["ep_id"] == "ep-1"
    assert call["new_body"] == "corrected"
    assert call["reason"] == "fix"
    assert call["by"] == {"source_type": "human"}


def test_improve_new_body_cap_fires(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="new-body"):
        run_improve(
            recording, ep_id="ep-1", new_body="x" * (BODY_MAX_CHARS + 1),
            fmt="human", out=_out(),
        )
    assert recording.improve_calls == []


def test_improve_reason_cap_fires(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="reason"):
        run_improve(
            recording, ep_id="ep-1", new_body="x",
            reason="r" * (REASON_MAX_CHARS + 1), fmt="human", out=_out(),
        )
    assert recording.improve_calls == []


def test_forget_delegates_with_now(recording: RecordingFacade):
    run_forget(
        recording, ep_id="ep-1", reason="obsolete", source_type="human",
        now="2026-07-16T12:00:00Z", fmt="human", out=_out(),
    )
    call = recording.forget_calls[0]
    assert call["ep_id"] == "ep-1"
    assert call["reason"] == "obsolete"
    assert call["by"] == {"source_type": "human"}
    assert call["now"] == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_forget_reason_required_and_capped(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="reason"):
        run_forget(
            recording, ep_id="ep-1", reason="r" * (REASON_MAX_CHARS + 1),
            fmt="human", out=_out(),
        )
    assert recording.forget_calls == []


def test_forget_bad_now_raises_usage(recording: RecordingFacade):
    with pytest.raises(CliUsageError, match="now"):
        run_forget(
            recording, ep_id="ep-1", reason="x", now="bad",
            fmt="human", out=_out(),
        )
    assert recording.forget_calls == []


# ---------------------------------------------------------------------------
# expire / revalidate — SO-14-05: CLI-intercepted, never reaches the facade.
# ---------------------------------------------------------------------------


def test_expire_revalidate_raises_cli_not_in_mvp0():
    with pytest.raises(CliNotInMVP0) as exc_info:
        run_expire_revalidate("expire")
    assert exc_info.value.command == "expire"
    assert exc_info.value.exit_code == 75


def test_revalidate_raises_cli_not_in_mvp0():
    with pytest.raises(CliNotInMVP0) as exc_info:
        run_expire_revalidate("revalidate")
    assert exc_info.value.command == "revalidate"