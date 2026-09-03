"""Tests for ``seahorse.observe.spool`` — the hook-side lossless spool (3B).

The spool covers only the gap between the hook and the queue: the hook writes
undeliverable envelopes as JSON files; the observer drains them into the
queue DB at startup (the worker stays the single DB writer).
"""

from __future__ import annotations

import json

from seahorse.observe.queue import ObserverQueue
from seahorse.observe.spool import drain_spool, spool_event

RAW = {
    "schema_version": "1.0",
    "session_id": "sess-1",
    "event_type": "user_prompt_submit",
    "payload": {"prompt": "hello"},
}


def test_spool_event_writes_parseable_json(tmp_path) -> None:
    spool = tmp_path / "spool"
    assert spool_event(spool, RAW) is True
    files = sorted(spool.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == RAW
    assert not list(spool.glob("*.tmp"))  # atomic replace — no torn files


def test_spool_event_respects_max_files_cap(tmp_path, monkeypatch) -> None:
    import seahorse.observe.spool as spool_mod

    monkeypatch.setattr(spool_mod, "_MAX_FILES", 2)
    spool = tmp_path / "spool"
    assert spool_event(spool, RAW) is True
    assert spool_event(spool, RAW) is True
    assert spool_event(spool, RAW) is False  # cap reached — honest skip
    assert len(list(spool.glob("*.json"))) == 2


def test_drain_spool_enqueues_and_deletes(tmp_path) -> None:
    spool = tmp_path / "spool"
    spool_event(spool, RAW)
    spool_event(spool, {**RAW, "event_type": "stop", "payload": {}})
    queue = ObserverQueue(tmp_path / "observer.db")
    try:
        drained = drain_spool(spool, queue)
        assert drained == 2
        assert list(spool.glob("*.json")) == []  # consumed
        pending = queue.pending()
        assert len(pending) == 2
        assert {env.event_type for _, env in pending} == {"user_prompt_submit", "stop"}
    finally:
        queue.close()


def test_drain_spool_dedups_via_queue_fingerprint(tmp_path) -> None:
    """A re-spooled duplicate is a queue no-op (dedup layer 1) — and the file
    is still consumed."""
    spool = tmp_path / "spool"
    spool_event(spool, RAW)
    spool_event(spool, dict(RAW))  # identical payload → same fingerprint
    queue = ObserverQueue(tmp_path / "observer.db")
    try:
        assert drain_spool(spool, queue) == 2
        assert len(queue.pending()) == 1
    finally:
        queue.close()


def test_drain_spool_deletes_unparseable_files(tmp_path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir(parents=True)
    (spool / "poison.json").write_text("{not json", encoding="utf-8")
    (spool / "wrong-shape.json").write_text(
        json.dumps({"session_id": "s"}), encoding="utf-8"  # missing event_type
    )
    queue = ObserverQueue(tmp_path / "observer.db")
    try:
        assert drain_spool(spool, queue) == 0
        assert list(spool.glob("*.json")) == []  # poison never blocks the drain
        assert queue.pending() == []
    finally:
        queue.close()


def test_drain_spool_noop_when_dir_missing(tmp_path) -> None:
    queue = ObserverQueue(tmp_path / "observer.db")
    try:
        assert drain_spool(tmp_path / "does-not-exist", queue) == 0
    finally:
        queue.close()


def test_roundtrip_hook_to_queue(tmp_path) -> None:
    """The full path: spool_event writes what parse_envelope accepts, so a
    spooled event lands in the queue as a real Envelope."""
    spool = tmp_path / "spool"
    spool_event(spool, RAW)
    queue = ObserverQueue(tmp_path / "observer.db")
    try:
        drain_spool(spool, queue)
        _event_id, env = queue.pending()[0]
        assert env.session_id == "sess-1"
        assert env.payload == {"prompt": "hello"}
    finally:
        queue.close()