"""Tests for the reader-context assembler seam (``harness/context.py``).

The seam makes the reader's context representation configurable
(summary | body | body_bounded) so the reader-context A/B can measure whether
hydrating the FULL body (the ``rerank_body`` signal) closes the ``reader_bottleneck``
gap (e2e 0.070 vs recall@10 0.790). The assembler is pure — ``body_for`` is
injected — and ``batch_body_for`` is the thin facade wiring layer.
"""

from __future__ import annotations

from seahorse.benchmark.harness.context import (
    BODY_MAX_CHARS,
    assemble_context,
    batch_body_for,
)
from seahorse.disclosure.types import MAX_FULL_BATCH


def _row(ep_id: str, subject: str, summary: str | None = None):
    class _Row:
        pass

    r = _Row()
    r.ep_id = ep_id
    r.subject = subject
    r.summary = summary
    return r


class _Detail:
    """FullDetail-like double (the episode carries the body)."""

    def __init__(self, ep_id: str, body: str):
        self.episode = type("Ep", (), {"id": ep_id, "body": body})()


class _Facade:
    """recall_full double that records the batch sizes it receives."""

    def __init__(self, bodies: dict[str, str]):
        self._bodies = bodies
        self.batch_sizes: list[int] = []

    def recall_full(self, ep_ids):
        self.batch_sizes.append(len(ep_ids))
        return [
            _Detail(ep_id, self._bodies[ep_id])
            for ep_id in ep_ids
            if ep_id in self._bodies
        ]


def test_summary_mode_is_the_baseline():
    rows = [_row("e1", "Project A", "A project update.")]
    assert assemble_context(rows, mode="summary") == "1. [Project A] A project update."
    # The default mode IS summary — the baseline behavior never changes.
    assert assemble_context(rows) == "1. [Project A] A project update."


def test_summary_falls_back_to_subject():
    rows = [_row("e1", "Project A", None)]
    assert assemble_context(rows, mode="summary") == "1. [Project A] Project A"


def test_body_mode_hydrates_full_body():
    rows = [_row("e1", "Project A", "summary")]
    body_for = {"e1": "The FULL body with the mid-turn answer."}
    assert assemble_context(
        rows, mode="body", body_for=lambda ep: body_for.get(ep)
    ) == "1. [Project A]\nThe FULL body with the mid-turn answer."


def test_body_mode_falls_back_to_summary_on_missing_body():
    rows = [_row("e1", "Project A", "summary line")]
    assert assemble_context(
        rows, mode="body", body_for=lambda ep: None
    ) == "1. [Project A] summary line"


def test_body_bounded_truncates_to_budget():
    rows = [_row("e1", "Project A", "summary")]
    long_body = "x" * (BODY_MAX_CHARS + 500)
    rendered = assemble_context(
        rows, mode="body_bounded", body_for=lambda ep: long_body
    )
    body_line = rendered.split("\n", 1)[1]
    assert len(body_line) == BODY_MAX_CHARS


def test_body_bounded_keeps_short_bodies_untouched():
    rows = [_row("e1", "Project A", "summary")]
    short = "short body"
    rendered = assemble_context(
        rows, mode="body_bounded", body_for=lambda ep: short
    )
    assert rendered == "1. [Project A]\nshort body"


def test_unknown_mode_fails_loud():
    import pytest

    with pytest.raises(ValueError, match="unknown context mode"):
        assemble_context([_row("e1", "A", "s")], mode="full")


def test_multiple_rows_are_numbered():
    rows = [_row("e1", "A", "s1"), _row("e2", "B", "s2")]
    ctx = assemble_context(rows, mode="summary")
    assert ctx == "1. [A] s1\n2. [B] s2"


def test_batch_body_for_dedups_and_respects_batch_cap():
    bodies = {f"e{i}": f"body{i}" for i in range(8)}
    facade = _Facade(bodies)
    # 8 unique ids → ceil(8/5) = 2 batches, never above MAX_FULL_BATCH.
    result = batch_body_for(facade, [f"e{i}" for i in range(8)])
    assert result == bodies
    assert all(size <= MAX_FULL_BATCH for size in facade.batch_sizes)
    assert facade.batch_sizes == [5, 3]


def test_batch_body_for_skips_missing_episodes():
    facade = _Facade({"e1": "body1"})
    result = batch_body_for(facade, ["e1", "missing"])
    assert result == {"e1": "body1"}
