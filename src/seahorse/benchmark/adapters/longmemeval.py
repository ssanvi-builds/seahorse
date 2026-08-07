"""LongMemEval adapter — the reference benchmark for MVP-1 (f5-16 §4.1).

Loads from HuggingFace ``xiaowu0162/longmemeval-cleaned`` (lazy import of
``datasets`` — the benchmark extra). Tests use synthetic rows via ``_from_row``
(no HF download in CI). ``_LOADER_CODE_SHA256`` is the trust_remote_code audit
hash of the dataset loading script.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from seahorse.benchmark.adapters.base import parse_date
from seahorse.benchmark.adapters.registry import AdapterRegistry
from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance

_LMEB_QUESTION_TYPE_TO_COGNITIVE: dict[str, str] = {
    "single-session-user": "episodic",
    "single-session-assistant": "episodic",
    "single-session-preference": "semantic",
    "multi-session": "semantic",
    "knowledge-update": "semantic",
    "temporal-reasoning": "semantic",
    "abstention": "n/a",
    # NOTE: "procedural" is NOT covered by LongMemEval-S; declared as a gap.
}

_LMEB_CAPABILITY_MAP: dict[str, tuple[str, ...]] = {
    "single-session-user": ("information-extraction",),
    "single-session-assistant": ("information-extraction",),
    "single-session-preference": ("information-extraction",),
    "multi-session": ("multi-session-reasoning",),
    "knowledge-update": ("knowledge-update",),
    "temporal-reasoning": ("temporal-reasoning",),
    "abstention": ("abstention",),
}


@AdapterRegistry.register("lmeb")
class LMEBLoader:
    """LongMemEval adapter — loads from HuggingFace xiaowu0162/longmemeval-cleaned."""

    _DATASET_HF_REPO = "xiaowu0162/longmemeval-cleaned"
    _DATASET_VERSION = "1.0.0"
    _LOADER_CODE_SHA256 = "unset"  # SHA-256 of the HF dataset loading script (audit)

    @staticmethod
    def name() -> str:
        return "LongMemEval"

    @staticmethod
    def available_configs() -> tuple[str, ...]:
        return ("s",)  # MVP-1: only "s"

    @staticmethod
    def load(config: BenchmarkConfig) -> BenchmarkDataset:
        try:
            import importlib

            datasets = importlib.import_module("datasets")  # the 'benchmark' extra
        except ImportError as exc:
            raise RuntimeError(
                "install seahorse[benchmark] to load LongMemEval (datasets extra)"
            ) from exc

        hf_config = config.dataset_config
        ds = datasets.load_dataset(
            LMEBLoader._DATASET_HF_REPO,
            name=f"longmemeval_{hf_config}",
            revision=config.dataset_revision,  # commit hash, NOT tag
            split="test",
            trust_remote_code=True,  # audited via _LOADER_CODE_SHA256
        )
        instances = [LMEBLoader._from_row(row) for row in ds]
        split_hash = hashlib.sha256(
            json.dumps(
                [asdict(i) for i in instances], sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        return BenchmarkDataset(
            name=f"longmemeval-{hf_config}",
            version=LMEBLoader._DATASET_VERSION,
            config=hf_config,
            split_hash=split_hash,
            loader_code_sha256=LMEBLoader._LOADER_CODE_SHA256,
            instances=tuple(instances),
            metadata={
                "total_questions": len(instances),
                "hf_repo": LMEBLoader._DATASET_HF_REPO,
            },
        )

    @staticmethod
    def _from_row(row: dict) -> BenchmarkInstance:
        """Map a HF row to a canonical ``BenchmarkInstance`` (testable)."""
        q_type = row["question_type"]
        is_abstention = q_type == "abstention" or str(row.get("question_id", "")).endswith(
            "_abs"
        )
        return BenchmarkInstance(
            instance_id=str(row["question_id"]),
            question=row["question"],
            golden_answer=row.get("answer"),
            golden_session_ids=tuple(row.get("answer_session_ids", [])),
            golden_evidence=(),
            question_type=q_type,
            capabilities=_LMEB_CAPABILITY_MAP.get(q_type, ()),
            cognitive_category=_LMEB_QUESTION_TYPE_TO_COGNITIVE.get(q_type, "n/a"),
            question_date=parse_date(row.get("question_date")),
            haystack=tuple(row.get("haystack_sessions", [])),
            abstention=is_abstention,
        )


__all__ = ["LMEBLoader"]
