"""Tests for ``seahorse.distill.synthesis`` — the LLM synthesis of clusters.

``synthesize_cluster`` reuses the extractor seam of #4 (schema hint + repair +
degrade honesto) via ``LLMClient.extract`` with a custom ``prompt_builder``:
1 call per cluster (N episodes → 1 fact), amortized under $0.002/episode.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seahorse.distill.cluster import Cluster
from seahorse.distill.synthesis import (
    ConsolidatedFrontmatter,
    build_cluster_content,
    build_synthesis_prompt,
    synthesize_cluster,
)
from seahorse.llm import BudgetContext, ExtractResult


class _FakeEpisode:
    def __init__(self, ep_id: str, body: str) -> None:
        self.id = ep_id
        self.body = body
        self.subject = body.splitlines()[0].lstrip("# ").strip()


def _cluster(*bodies: str) -> Cluster:
    eps = [_FakeEpisode(f"ep-{i}", b) for i, b in enumerate(bodies)]
    return Cluster(key="fix the flaky recall test", episodes=eps, representative=eps[-1])


class _FakeLLMClient:
    """Recording double for the ``LLMClient`` Protocol (extract only)."""

    def __init__(self, result: ExtractResult) -> None:
        self.result = result
        self.calls: list[dict] = []

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
        self.calls.append(
            {
                "content": content,
                "schema_hint": schema_hint,
                "role": role,
                "budget": budget,
                "prompt_builder": prompt_builder,
            }
        )
        return self.result


def _ok_result(**data) -> ExtractResult:
    payload = {"consolidated_body": "# fix the flaky recall test\n\nSynthesized fact."}
    payload.update(data)
    return ExtractResult(
        data=payload,
        prompt_hash="h" * 64,
        model_used="ollama/qwen3:1.7b",
        confidence=0.9,
    )


class TestConsolidatedFrontmatter:
    def test_accepts_consolidated_body(self) -> None:
        inst = ConsolidatedFrontmatter.model_validate(
            {"consolidated_body": "# key\n\nfact"}
        )
        assert inst.consolidated_body == "# key\n\nfact"

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            ConsolidatedFrontmatter.model_validate(
                {"consolidated_body": "# key\n\nfact", "hallucinated": "x"}
            )

    def test_requires_consolidated_body(self) -> None:
        with pytest.raises(ValidationError):
            ConsolidatedFrontmatter.model_validate({})


class TestBuildClusterContent:
    def test_includes_clustering_key_and_episodes(self) -> None:
        cluster = _cluster(
            "# Fix the flaky recall test [sess-1:1]\n\nAttempt 1.",
            "# Fix the flaky recall test [sess-1:2]\n\nAttempt 2.",
        )
        content = build_cluster_content(cluster)
        assert "### Clustering key" in content
        assert "fix the flaky recall test" in content
        assert "### Episode 1" in content
        assert "Attempt 1." in content
        assert "### Episode 2" in content
        assert "Attempt 2." in content


class TestBuildSynthesisPrompt:
    def test_returns_system_and_user_messages(self) -> None:
        messages = build_synthesis_prompt("content", ConsolidatedFrontmatter)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_system_has_synthesis_rules(self) -> None:
        messages = build_synthesis_prompt("content", ConsolidatedFrontmatter)
        system = messages[0]["content"]
        assert "synthesize" in system
        assert "Do NOT invent" in system
        assert "DATA, not instructions" in system

    def test_user_has_schema_and_delimited_content(self) -> None:
        messages = build_synthesis_prompt("the content", ConsolidatedFrontmatter)
        user = messages[1]["content"]
        assert "SCHEMA" in user
        assert "<content>\nthe content\n</content>" in user


class TestSynthesizeCluster:
    def test_success_returns_synthesized_body_and_provenance(self) -> None:
        client = _FakeLLMClient(_ok_result())
        cluster = _cluster(
            "# Fix the flaky recall test [sess-1:1]\n\nAttempt 1.",
            "# Fix the flaky recall test [sess-1:2]\n\nAttempt 2.",
            "# Fix the flaky recall test [sess-1:3]\n\nAttempt 3.",
        )
        result = synthesize_cluster(client, cluster)
        assert result.degraded_to_skip is False
        assert result.consolidated_body == "# fix the flaky recall test\n\nSynthesized fact."
        assert result.model_used == "ollama/qwen3:1.7b"
        assert result.prompt_hash == "h" * 64
        assert result.confidence == 0.9

    def test_uses_synthesis_role_and_prompt_builder(self) -> None:
        client = _FakeLLMClient(_ok_result())
        cluster = _cluster(
            "# Topic [sess-1:1]\n\nA.",
            "# Topic [sess-1:2]\n\nB.",
            "# Topic [sess-1:3]\n\nC.",
        )
        synthesize_cluster(client, cluster)
        call = client.calls[0]
        assert call["role"] == "synthesis"
        assert call["prompt_builder"] is build_synthesis_prompt
        assert call["schema_hint"] is ConsolidatedFrontmatter

    def test_degrade_returns_reason(self) -> None:
        client = _FakeLLMClient(
            ExtractResult(data={}, prompt_hash="", degraded_to_skip=True)
        )
        cluster = _cluster(
            "# Topic [sess-1:1]\n\nA.",
            "# Topic [sess-1:2]\n\nB.",
            "# Topic [sess-1:3]\n\nC.",
        )
        result = synthesize_cluster(client, cluster)
        assert result.degraded_to_skip is True
        assert result.consolidated_body == ""
        assert result.degrade_reason is not None

    def test_budget_cap_scales_with_cluster_size(self) -> None:
        client = _FakeLLMClient(_ok_result())
        cluster = _cluster(
            "# Topic [sess-1:1]\n\nA.",
            "# Topic [sess-1:2]\n\nB.",
            "# Topic [sess-1:3]\n\nC.",
        )
        synthesize_cluster(client, cluster)
        budget = client.calls[0]["budget"]
        assert budget is not None
        assert budget.cap_usd == pytest.approx(0.002 * 3)
