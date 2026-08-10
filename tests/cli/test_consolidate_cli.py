"""Tests for ``seahorse consolidate`` — the distillation CLI command (§5.3).

The command reads the vigente set, clusters by subject recurrence (N≥3), and
distills each cluster into a consolidated semantic knowledge note. Idempotent:
a cluster whose key already has a consolidated note is skipped (§5.5).
"""

from __future__ import annotations

import io

from seahorse.cli.primitives import run_consolidate
from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload


def _out() -> io.StringIO:
    return io.StringIO()


def test_consolidate_no_clusters(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        out = _out()
        run_consolidate(facade, fmt="human", out=out)
        assert "no clusters to distill" in out.getvalue()
    finally:
        storage.close()


def test_consolidate_distills_cluster(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            facade.remember(
                RememberPayload(
                    body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                    by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
                )
            )
        out = _out()
        run_consolidate(facade, fmt="human", out=out)
        text = out.getvalue()
        assert "consolidated: topic (3 sources)" in text
        # Idempotent: the second run skips the already-consolidated key.
        out2 = _out()
        run_consolidate(facade, fmt="human", out=out2)
        assert "no clusters to distill" in out2.getvalue()
    finally:
        storage.close()


def test_consolidate_json(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        out = _out()
        run_consolidate(facade, fmt="json", out=out)
        import json

        payload = json.loads(out.getvalue())
        assert payload["clusters_found"] == 0
        assert payload["items"] == []
    finally:
        storage.close()
