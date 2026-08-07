"""F7 experiment variants — the config matrices for (a) recency and (c) embed.

Each variant is a ``(score_source, recency_config, embed_mode)`` tuple that the
experiment runner folds into a ``BenchmarkConfig`` (f7-experimental-design §3:
``score_source`` is the manifest variant). The matrices are fixed by the spec:

- (a) recency: baseline ``mvp1_rrf`` (recency OFF) + 9-combo sweep of
  γ ∈ {0.25, 0.5, 1.0} × half_life ∈ {7, 30, 90} days (f7 §5(a)).
- (c) embed: baseline ``embed_mode=body`` vs ``body+summary`` (f7 §5(c)).

Pure data — no imports beyond the config types (keeps the module stdlib-light).
"""

from __future__ import annotations

from dataclasses import dataclass

from seahorse.benchmark.config import ScoreSource

# The F1 sweep grid (f7 §5(a)): γ × half_life, all 9 combos.
RECENCY_SWEEP_GAMMAS = (0.25, 0.5, 1.0)
RECENCY_SWEEP_HALF_LIVES_DAYS = (7, 30, 90)

# Experiment kinds the runner understands.
EXPERIMENTS = ("recency", "embed")

# Corpora: synthetic (mechanical CI verification) or the real LMEB-S haystack.
CORPORA = ("synthetic", "lmeb-s")


@dataclass(frozen=True)
class ExperimentVariant:
    """A single experiment configuration (the manifest's ``score_source``)."""

    name: str
    score_source: ScoreSource
    recency_config: dict | None = None
    embed_mode: str = "body"
    description: str = ""

    def as_config_kwargs(self) -> dict:
        """The ``BenchmarkConfig`` overrides this variant carries (f7 §3)."""
        return {
            "score_source": self.score_source,
            "recency_config": self.recency_config,
            "embed_mode": self.embed_mode,
        }


def recency_variants() -> tuple[ExperimentVariant, ...]:
    """Baseline (recency OFF, ``mvp1_rrf``) + the 9-combo sweep (ON)."""
    baseline = ExperimentVariant(
        name="mvp1_rrf",
        score_source="mvp1_rrf",
        description="baseline — recency OFF, pure RRF (ADR-10 fingerprint)",
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
    """Baseline (``embed_mode=body``) vs the F3 candidate (``body+summary``)."""
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
            description="F3 candidate — embed body+summary",
        ),
    )


def variants_for(experiment: str) -> tuple[ExperimentVariant, ...]:
    """The variant matrix for an experiment kind (f7 §5)."""
    if experiment == "recency":
        return recency_variants()
    if experiment == "embed":
        return embed_variants()
    raise ValueError(f"unknown experiment: {experiment!r} (expected {EXPERIMENTS!r})")


__all__ = [
    "EXPERIMENTS",
    "CORPORA",
    "RECENCY_SWEEP_GAMMAS",
    "RECENCY_SWEEP_HALF_LIVES_DAYS",
    "ExperimentVariant",
    "recency_variants",
    "embed_variants",
    "variants_for",
]
