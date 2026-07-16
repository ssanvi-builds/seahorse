"""Tests for the Python → wire JSON serializer (#13)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from seahorse.facade.errors import SeahorseError
from seahorse.mcp.serialize import (
    _iso_z,
    success_response,
    to_json,
    to_text_content,
    to_wire,
)
from tests.mcp.conftest import make_full_detail, make_timeline_window, make_write_result


class TestIsoZ:
    def test_utc_datetime_to_z(self) -> None:
        s = _iso_z(datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC))
        assert s == "2026-07-16T12:00:00Z"

    def test_microseconds_preserved(self) -> None:
        s = _iso_z(datetime(2026, 7, 16, 12, 0, 0, 123456, tzinfo=UTC))
        assert s == "2026-07-16T12:00:00.123456Z"

    def test_non_utc_offset_converted_to_z(self) -> None:
        # 14:00 +02:00 == 12:00 UTC → Z
        from datetime import timezone

        dt = datetime(2026, 7, 16, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        assert _iso_z(dt) == "2026-07-16T12:00:00Z"

    def test_naive_assumed_utc(self) -> None:
        s = _iso_z(datetime(2026, 7, 16, 12, 0, 0))
        assert s == "2026-07-16T12:00:00Z"


class TestPrimitives:
    def test_none_passthrough(self) -> None:
        assert to_wire(None) is None

    def test_string_passthrough(self) -> None:
        assert to_wire("hi") == "hi"

    def test_int_passthrough(self) -> None:
        assert to_wire(42) == 42

    def test_float_passthrough(self) -> None:
        assert to_wire(3.14) == 3.14

    def test_bool_passthrough(self) -> None:
        assert to_wire(True) is True
        assert to_wire(False) is False

    def test_uuid_to_str(self) -> None:
        u = uuid4()
        assert to_wire(u) == str(u)


class TestCollections:
    def test_tuple_to_array(self) -> None:
        assert to_wire((1, 2, 3)) == [1, 2, 3]

    def test_list_to_array(self) -> None:
        assert to_wire([1, 2]) == [1, 2]

    def test_set_to_sorted_is_not_required_but_is_array(self) -> None:
        out = to_wire({1, 2})
        assert isinstance(out, list)
        assert set(out) == {1, 2}

    def test_frozenset_to_array(self) -> None:
        out = to_wire(frozenset({"a", "b"}))
        assert isinstance(out, list)
        assert set(out) == {"a", "b"}

    def test_dict_walks_values(self) -> None:
        out = to_wire({"t": datetime(2026, 7, 16, tzinfo=UTC), "n": 1})
        assert out == {"t": "2026-07-16T00:00:00Z", "n": 1}


class TestExcludeNoneFalse:
    """nulls are explicit (R9 f5-13) — shape stable MVP-0 → MVP-1."""

    def test_none_value_kept_as_null(self) -> None:
        out = to_wire({"a": None, "b": 1})
        assert out == {"a": None, "b": 1}

    def test_json_dumps_keeps_null(self) -> None:
        s = to_json({"a": None})
        assert s == '{"a": null}'


class TestDataclassSerialization:
    def test_write_result_serializes(self) -> None:
        wr = make_write_result()
        out = to_wire(wr)
        assert out == {
            "ep_id": "ep-1",
            "fact_id": "fact-1",
            "status": "ACTIVE",
            "collisions_detected": [],
        }

    def test_write_result_collisions_always_empty_in_mvp0(self) -> None:
        wr = make_write_result(status="COLLISION")
        # collisions_detected is [] by construction; serialized verbatim
        assert to_wire(wr)["collisions_detected"] == []

    def test_episode_datetimes_become_z(self) -> None:
        from tests.mcp.conftest import make_episode

        ep = make_episode()
        out = to_wire(ep)
        assert out["created_at"] == "2026-07-16T12:00:00Z"
        assert out["schema_version"] == "1.1"
        assert out["expired_at"] is None  # exclude_none=False

    def test_nested_full_detail_recurses(self) -> None:
        fd = make_full_detail()
        out = to_wire(fd)
        # episode nested → datetime canonicalized
        assert out["episode"]["created_at"] == "2026-07-16T12:00:00Z"
        # provenance nested dataclass
        assert out["provenance"]["agent_id"] == "a1"
        # freshness nested dataclass
        assert out["freshness"]["regime"] == "agent"
        # pit null kept (exclude_none=False)
        assert out["pit"] is None

    def test_nested_timeline_window_entries_tuple_to_array(self) -> None:
        tw = make_timeline_window()
        out = to_wire(tw)
        assert out["anchor_ep_id"] == "ep-2"
        assert isinstance(out["entries"], list)
        assert len(out["entries"]) == 2
        # entries are TimelineEntry dataclasses → nested datetime canonicalized
        assert out["entries"][1]["supersedes"] == "ep-1"
        assert out["entries"][1]["created_at"] == "2026-07-16T12:00:00Z"
        # score defaults to None and is kept (exclude_none=False)
        assert out["entries"][0]["score"] is None


class TestSeahorseErrorSerialization:
    def test_seahorse_error_to_code_detail(self) -> None:
        err = SeahorseError(code="E_EMPTY_BODY", detail="body must be non-empty")
        out = to_wire(err)
        assert out == {"code": "E_EMPTY_BODY", "detail": "body must be non-empty"}


class TestTextContentAndResponse:
    def test_to_text_content_wraps_json(self) -> None:
        block = to_text_content({"a": 1})
        assert block["type"] == "text"
        assert json.loads(block["text"]) == {"a": 1}

    def test_success_response_shape(self) -> None:
        resp = success_response(7, {"a": 1})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 7
        assert resp["result"]["isError"] is False
        assert isinstance(resp["result"]["content"], list)
        assert resp["result"]["content"][0]["type"] == "text"
        assert json.loads(resp["result"]["content"][0]["text"]) == {"a": 1}

    def test_success_response_preserves_null(self) -> None:
        resp = success_response("r1", {"a": None})
        assert json.loads(resp["result"]["content"][0]["text"]) == {"a": None}

    def test_to_json_non_ascii(self) -> None:
        # ensure_ascii=False — Spanish characters pass through verbatim
        assert to_json({"city": "Madrid"}) == '{"city": "Madrid"}'
        assert to_json({"name": "Sergio"}) == '{"name": "Sergio"}'