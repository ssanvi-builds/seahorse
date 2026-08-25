"""Experiment variants — the config matrices for (a) recency, (b) rerank, (c) embed,
(d) decay.

Each variant is a ``(score_source, recency_config, decay_config, embed_mode,
rerank_enabled)`` tuple that the experiment runner folds into a
``BenchmarkConfig`` (``score_source`` is the manifest variant). The matrices
are fixed by the spec:

- (a) recency: baseline ``mvp1_rrf`` (recency OFF) + 9-combo sweep of
  γ ∈ {0.25, 0.5, 1.0} × half_life ∈ {7, 30, 90} days.
- (b) rerank: baseline ``mvp1_rrf`` (rerank OFF) vs ``rrf_rerank`` (ON,
  k_rerank≈20, text = summary/subject).
- (c) embed: baseline ``embed_mode=body`` vs ``body+summary``.
- (d) decay (Sprint D): baseline ``mvp1_rrf`` (decay OFF) vs ``mvp1_decay``
  (ON with the R3 S₀ priors). The R3 priors import from
  ``seahorse.retrieval.constants`` (single source — a duplicated table would
  drift from the core pins); the module stays otherwise pure data.
"""

from __future__ import annotations

from dataclasses import dataclass

from seahorse.benchmark.config import ScoreSource
from seahorse.retrieval.constants import (
    DECAY_DEFAULT_HALF_LIFE_DAYS,
    DECAY_HALF_LIVES_BY_TYPE,
)

# The recency sweep grid: γ × half_life, all 9 combos.
RECENCY_SWEEP_GAMMAS = (0.25, 0.5, 1.0)
RECENCY_SWEEP_HALF_LIVES_DAYS = (7, 30, 90)

# The rerank over-fetch: the RRF fusion runs with k_rerank so the
# cross-encoder has ~20 candidates to reorder before truncating to k.
RERANK_OVERFETCH_K = 20

# Experiment kinds the runner understands.
EXPERIMENTS = (
    "recency",
    "rerank",
    "embed",
    "decay_rrf",
    "batch",
    "multi_hop",
    "entity_centric",
    "decay",
    "skills",
    "rrf_k",
    "rerank_body",
    "end_to_end",
    "reader_context",
    "episode_granularity",
)

# Corpora: synthetic (mechanical CI verification), the real LMEB-S haystack,
# or the real claude-mem corpus (per-turn batching).
CORPORA = ("synthetic", "lmeb-s", "claude-mem")


@dataclass(frozen=True)
class ExperimentVariant:
    """A single experiment configuration (the manifest's ``score_source``)."""

    name: str
    score_source: ScoreSource
    recency_config: dict | None = None
    decay_config: dict | None = None
    embed_mode: str = "body+summary"  # embedding flip default
    rerank_enabled: bool = False
    description: str = ""

    def as_config_kwargs(self) -> dict:
        """The ``BenchmarkConfig`` overrides this variant carries."""
        return {
            "score_source": self.score_source,
            "recency_config": self.recency_config,
            "decay_config": self.decay_config,
            "embed_mode": self.embed_mode,
            "rerank_enabled": self.rerank_enabled,
        }


def recency_variants() -> tuple[ExperimentVariant, ...]:
    """Baseline (recency OFF, ``mvp1_rrf``) + the 9-combo sweep (ON)."""
    baseline = ExperimentVariant(
        name="mvp1_rrf",
        score_source="mvp1_rrf",
        description="baseline — recency OFF, pure RRF (honest fingerprint)",
    )
    sweep = tuple(
        ExperimentVariant(
            name=f"recency_g{g:g}_hl{hl:g}",
            score_source="mvp1_rrf_recency",
            recency_config={"gamma": g, "half_life_days": float(hl)},
            description=f"recency ON — gamma={g}, half_life={hl}d",
        )
        for g in RECENCY_SWEEP_GAMMAS
        for hl in RECENCY_SWEEP_HALF_LIVES_DAYS
    )
    return (baseline,) + sweep


def embed_variants() -> tuple[ExperimentVariant, ...]:
    """Baseline (``embed_mode=body``) vs the embedding candidate (``body+summary``)."""
    return (
        ExperimentVariant(
            name="embed_body",
            score_source="mvp1_rrf",
            embed_mode="body",
            description="baseline — embed body only",
        ),
        ExperimentVariant(
            name="embed_body_summary",
            score_source="mvp1_rrf",
            embed_mode="body+summary",
            description="embedding candidate — embed body+summary",
        ),
    )


def decay_variants() -> tuple[ExperimentVariant, ...]:
    """Baseline (decay OFF, ``mvp1_rrf``) vs the decay candidate (``mvp1_decay``).

    The decay candidate wires the R3 S₀ priors (per-type half-lives + the
    conservative general-knowledge default) as the composition-root swap —
    the same config the CLI ``--decay-*`` flags produce. The standalone
    ``experiment decay`` (F7(g) FAMA/MPA measurement) is separate and stays
    untouched.
    """
    return (
        ExperimentVariant(
            name="mvp1_rrf",
            score_source="mvp1_rrf",
            description="baseline — decay OFF, pure RRF (honest fingerprint)",
        ),
        ExperimentVariant(
            name="mvp1_decay",
            score_source="mvp1_decay",
            decay_config={
                "half_lives": dict(DECAY_HALF_LIVES_BY_TYPE),
                "default_half_life_days": DECAY_DEFAULT_HALF_LIFE_DAYS,
            },
            description=(
                "decay candidate — Ebbinghaus downweight by created_at age, "
                "S0 priors by cognitive_type (default 347d)"
            ),
        ),
    )


def rerank_variants() -> tuple[ExperimentVariant, ...]:
    """Baseline (rerank OFF, ``mvp1_rrf``) vs the rerank candidate (``rrf_rerank``).

    The rerank variant over-fetches to ``k_rerank`` and scores summary/subject
    (NOT body) — the cross-encoder reorders within the top-k.
    """
    return (
        ExperimentVariant(
            name="mvp1_rrf",
            score_source="mvp1_rrf",
            description="baseline — RRF only (honest fingerprint)",
        ),
        ExperimentVariant(
            name="rrf_rerank",
            score_source="rrf_rerank",
            rerank_enabled=True,
            description=(
                f"rerank candidate — RRF + cross-encoder rerank (k_rerank={RERANK_OVERFETCH_K}, "
                "text=summary/subject)"
            ),
        ),
    )


def variants_for(experiment: str) -> tuple[ExperimentVariant, ...]:
    """The variant matrix for an experiment kind."""
    if experiment == "recency":
        return recency_variants()
    if experiment == "rerank":
        return rerank_variants()
    if experiment == "embed":
        return embed_variants()
    if experiment == "decay_rrf":
        return decay_variants()
    raise ValueError(f"unknown experiment: {experiment!r} (expected {EXPERIMENTS!r})")


__all__ = [
    "EXPERIMENTS",
    "CORPORA",
    "RECENCY_SWEEP_GAMMAS",
    "RECENCY_SWEEP_HALF_LIVES_DAYS",
    "RERANK_OVERFETCH_K",
    "ExperimentVariant",
    "recency_variants",
    "embed_variants",
    "rerank_variants",
    "decay_variants",
    "variants_for",
]
