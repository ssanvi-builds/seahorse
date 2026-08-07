"""Run manifest — deterministic fingerprint + separate execution metadata (f5-16 §5.5).

The ``PinningFingerprint`` is the reproducibility contract: byte-identical
between identical runs (NO timestamps, NO environment specifics). The
``run_id`` derives ONLY from the fingerprint. ``ExecutionMetadata`` is the
non-deterministic layer (timestamps, environment) and lives in a separate
section.

OQ-16-12 (closed): the indexer is synchronous (``StubWritePath.ingest`` calls
``indexer.index_episode()`` in the same ``atomic()``), so the embeddings
barrier is a no-op — the manifest reports ``embedding_batch_config:
"batch_size=1_forced"`` and ``knn_completeness: 1.0``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from seahorse.benchmark.contracts import MetricReport


def _canonical(obj: Any) -> Any:
    """Recursively convert tuples to lists for canonical JSON serialization."""
    if isinstance(obj, dict):
        return {k: _canonical(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    return obj


@dataclass(frozen=True)
class PinningFingerprint:
    """Deterministic layer — byte-identical between identical runs.

    NO timestamps, NO environment specifics. ``score_source`` is the experiment
    variant (f7 §3): ``mvp1_rrf`` | ``mvp1_rrf_recency`` | ``rrf_rerank`` |
    ``fallback_g2`` (honest detected regime).
    """

    config_hash: str  # SHA-256 of BenchmarkConfig (canonical JSON, sort_keys=True)
    dataset_hash: str  # Dataset.split_hash
    loader_code_sha256: str  # dataset loader code hash (trust_remote_code audit)
    embedding_identity: str  # "me5-small:384:<sha12>:int8"
    embedding_batch_config: str  # "batch_size=1_forced" (OQ-16-12)
    knn_completeness: float  # 1.0 — sync indexer, drain is a no-op (OQ-16-12)
    reader_model_used: str  # "ollama/qwen3:1.7b@sha256:<digest>"
    judge_model_used: str  # "ollama/qwen2.5:7b@sha256:<digest>" (family-disjoint)
    seahorse_version: str  # git SHA
    skeleton_version: str  # git SHA of the benchmark package
    reader_system_prompt_sha256: str
    # question_type -> rubric_hash
    judge_rubric_hashes: dict[str, str] = field(default_factory=dict)
    ingest_template_sha256: str = ""
    sut_name: str = "seahorse"
    sut_version: str = "0.1.0"
    temporal_mode: bool = False
    score_source: str = "mvp1_rrf"
    reproducibility_class: str = "local_near_deterministic"
    expected_match_rate: float = 0.956
    judge_validation_status: str = "unvalidated_with_small_model"

    @property
    def run_id(self) -> str:
        """run_id derived ONLY from the fingerprint (no timestamps)."""
        return hashlib.sha256(
            json.dumps(_canonical(asdict(self)), sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]


@dataclass(frozen=True)
class ExecutionMetadata:
    """Non-deterministic layer — varies between runs."""

    started_at: str  # ISO-8601 UTC
    finished_at: str | None = None
    environment: dict = field(default_factory=dict)  # docker_image, device, etc.


@dataclass(frozen=True)
class RunManifest:
    """The full run artifact: fingerprint + execution + metrics + results hash."""

    fingerprint: PinningFingerprint
    execution: ExecutionMetadata
    metrics: dict[str, MetricReport]  # nested structure (matches the JSON example)
    results_sha256: str | None = None  # SHA-256 of raw responses JSONL
    run_errors: list[str] = field(default_factory=list)  # skipped instance_ids (f5-16 §8.3)


def _manifest_data(manifest: RunManifest) -> dict:
    """Canonical dict form, with the derived ``run_id`` injected into the
    fingerprint section (the spec's manifest JSON includes it, f5-16 §6.4)."""
    data = _canonical(asdict(manifest))
    data["fingerprint"]["run_id"] = manifest.fingerprint.run_id
    return data


def write_manifest(manifest: RunManifest, path: Path) -> None:
    """Canonical serialization: sort_keys=True, indent=2, UTF-8, LF.

    The fingerprint section is byte-identical between identical runs. The
    round-trip assertion verifies the ACTUAL nested structure (f5-16 §5.5 F6).
    """
    data = _manifest_data(manifest)
    content = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False)
    path.write_text(content, encoding="utf-8")
    assert json.loads(path.read_text("utf-8")) == data


__all__ = ["PinningFingerprint", "ExecutionMetadata", "RunManifest", "write_manifest"]
