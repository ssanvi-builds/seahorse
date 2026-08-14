"""``BenchmarkConfig`` — frozen dataclass with all pinned parameters.

The config is the reproducibility contract: every parameter that affects a
score lives here, and ``config_hash`` (SHA-256 of the canonical JSON) is part
of the ``PinningFingerprint``. ``validate()`` is the startup gate that rejects
``reader_model == judge_model`` (self-preference mitigation) and other invalid
combinations.

``score_source`` is the experiment variant: ``mvp1_rrf`` | ``mvp1_rrf_recency``
| ``rrf_rerank``. The SUT reports the honest detected regime in the manifest
(``fallback_g2`` when the hybrid retrieval is not wired).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

# The experiment variants the harness must support. ``fallback_g2`` is the
# honest detected regime when the hybrid retrieval is not wired.
ScoreSource = Literal["mvp1_rrf", "mvp1_rrf_recency", "rrf_rerank", "fallback_g2"]

# Reproducibility classes: local is near-deterministic (95.6% expected match),
# API is stochastic (22.1%).
ReproducibilityClass = Literal["local_near_deterministic", "api_stochastic"]


@dataclass(frozen=True)
class BenchmarkConfig:
    """All pinned parameters of a benchmark run (frozen, hashable)."""

    # Dataset
    adapter: str = "lmeb"
    dataset_config: str = "s"
    dataset_revision: str = "main"

    # Models (reader ≠ judge family — validate() enforces)
    reader_model: str = "ollama/qwen3:1.7b"
    judge_model: str = "ollama/qwen2.5:7b"
    reader_temperature: float = 0.0
    reader_seed: int = 42
    judge_temperature: float = 0.0
    judge_seed: int = 42
    max_tokens: int = 512

    # Retrieval
    top_k: int = 10
    temporal_mode: bool = False  # source_type='human' + valid_at=session_date
    score_source: ScoreSource = "mvp1_rrf"

    # Experiment variants (harness requirements, not options)
    recency_config: dict | None = None  # {"gamma": 0.5, "half_life_days": 30} | None
    rerank_enabled: bool = False
    rerank_model: str = ""  # pinned cross-encoder identity ("" when rerank OFF)
    embed_mode: str = "body+summary"  # "body" | "body+summary" — default embedding surface

    # Reproducibility
    repetitions: int = 1
    reproducibility_class: ReproducibilityClass = "local_near_deterministic"
    expected_match_rate: float = 0.956
    judge_validation_status: str = "unvalidated_with_small_model"

    # Output
    output_dir: str = "benchmark-output"
    sample_size: int = 10  # LevelProbeRunner sample

    def validate(self) -> None:
        """Startup gate: reject invalid parameter combinations.

        The load-bearing check is ``reader_model == judge_model`` — a judge of
        the same family as the reader inflates win-rate 10-25% (self-preference).
        Also rejects a non-positive ``top_k`` and an unknown ``score_source``.
        """
        if self.reader_model == self.judge_model:
            raise ValueError(
                "reader_model and judge_model must be different families "
                "(generator != judge); got both "
                f"{self.reader_model!r}"
            )
        if self.top_k <= 0:
            raise ValueError(f"top_k must be positive, got {self.top_k}")
        if self.score_source not in ("mvp1_rrf", "mvp1_rrf_recency", "rrf_rerank", "fallback_g2"):
            raise ValueError(f"unknown score_source: {self.score_source!r}")
        if self.embed_mode not in ("body", "body+summary"):
            raise ValueError(
                f"embed_mode must be 'body' or 'body+summary', got {self.embed_mode!r}"
            )
        if self.rerank_enabled and not self.rerank_model:
            raise ValueError(
                "rerank_enabled requires a pinned rerank_model (the cross-encoder "
                "identity goes in the fingerprint)"
            )

    def config_hash(self) -> str:
        """SHA-256 of the canonical JSON (sort_keys=True) — part of the fingerprint."""
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()


__all__ = ["BenchmarkConfig", "ScoreSource", "ReproducibilityClass"]
