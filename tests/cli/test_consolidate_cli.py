"""Tests for ``seahorse consolidate`` — the distillation CLI command.

The command reads the current-state set, clusters by subject recurrence (N≥3),
and distills each cluster into a consolidated semantic knowledge note.
Idempotent: a cluster whose key already has a consolidated note is skipped.
"""

from __future__ import annotations

import io

from seahorse.cli.primitives import run_consolidate
from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload
from seahorse.llm import BudgetContext, ExtractResult


def _out() -> io.StringIO:
    return io.StringIO()


class _FakeLLMClient:
    """Recording double for the ``LLMClient`` Protocol (extract only)."""

    def __init__(self, result: ExtractResult) -> None:
        self.result = result

    def extract(
        self,
        content: str,
        schema_hint: type,
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        prompt_builder=None,
    ) -> ExtractResult:
        return self.result


def _ok_result() -> ExtractResult:
    return ExtractResult(
        data={"consolidated_body": "# topic\n\nSynthesized knowledge."},
        prompt_hash="h" * 64,
        model_used="ollama/qwen3:1.7b",
        confidence=0.9,
    )


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


def test_consolidate_synthesis_llm_human_output(tmp_path) -> None:
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
        run_consolidate(
            facade,
            fmt="human",
            out=out,
            synthesis="llm",
            llm_client=_FakeLLMClient(_ok_result()),
        )
        text = out.getvalue()
        assert "consolidated: topic (3 sources)" in text
        assert "[llm]" in text  # the synthesis mode is reported
    finally:
        storage.close()


def test_consolidate_synthesis_option_accepted(vault) -> None:
    """``--synthesis llm`` is accepted by the Typer parser (no clusters → no-op)."""
    from tests.cli.conftest import invoke

    code, out, err = invoke(["--vault", str(vault), "consolidate", "--synthesis", "llm"])
    assert code == 0
    assert "no clusters to distill" in out
    assert err == ""


def test_consolidate_synthesis_llm_json_reports_mode(tmp_path) -> None:
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
        run_consolidate(
            facade,
            fmt="json",
            out=out,
            synthesis="llm",
            llm_client=_FakeLLMClient(_ok_result()),
        )
        import json

        payload = json.loads(out.getvalue())
        assert payload["items"][0]["synthesis"] == "llm"
    finally:
        storage.close()
