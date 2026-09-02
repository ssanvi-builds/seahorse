"""Decay experiment (FAMA-style) — decide Sprint D.

Falsifies the hypothesis "decay improves FAMA without damaging MPA". Decay is
modeled as a ranking bias (experiment priors, NOT grounding — R3): a
query-time downweight of episodes by age, ``score' = score * (1 - S *
age_factor)``, swept over ``S``. The A/B is decay ON (best ``S``) vs OFF
(``S = 0``).

Corpus: synthetic knowledge-update episodes — for each fact an OLD (obsolete)
version and a NEW (valid) version with ``created_at`` timestamps, plus
background distractors. The synthetic corpus verifies the harness MECHANICS in
CI (``HashEmbedder``, no model download) — NOT the science (fail-loud honesty).
The authoritative decision comes from an LMEB-S knowledge-update run.

Metrics:
- **FAMA-style**: fraction of fact queries where the OBSOLETE old version is
  NOT in the top-k (obsolete reuse avoided — the FAMA failure mode).
- **MPA** (memory presence accuracy): fraction of fact queries where the VALID
  new version IS in the top-k (valid info present).

Decision (``decide_decay``): Sprint D (decay ranking bias) is justified when
decay (best ``S``) improves FAMA-style by >= ``DECAY_FAMA_GAIN_THRESHOLD`` (5pp)
without damaging MPA by more than ``DECAY_MPA_DAMAGE_THRESHOLD`` (5pp).
Otherwise keep decay OFF (default, opt-in, like F1). Honest regime detection:
all-zero scores => ``fallback_g2`` => invalid decision (fail-loud honesty).

The corpus is designed to make the hypothesis FALSIFIABLE — three fact types:
- **Type A (decay helps)**: the OLD version is lexically closer to the query,
  so without decay it surfaces above the new version (FAMA failure); decay
  downweights it and the new version surfaces (FAMA pass).
- **Type B (decay neutral)**: the NEW version is lexically closer, so FAMA
  already passes without decay.
- **Type C (MPA fragile)**: the NEW version shares few tokens with the query
  (marginally in top-k); with aggressive decay it can fall out (MPA damage).

The exact numbers are NOT the science (fail-loud honesty).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark._tmpdirs import mkdtemp_scoped
from seahorse.benchmark.experiments.synthetic import HashEmbedder
from seahorse.contracts.episode import Episode
from seahorse.facade import build_facade
from seahorse.facade.types import FacadeConfig, Provenance, RememberPayload

# The k for the FAMA-style / MPA measurement (harness default).
DECAY_TOP_K = 10

# The over-fetch before the decay re-rank: the retrieval returns
# ``DECAY_OVERFETCH_K`` candidates and the decay bias re-ranks + truncates to
# ``DECAY_TOP_K`` (the real decay is a query-time reorder, not a truncation).
DECAY_OVERFETCH_K = 30

# The S sweep (decay strength). S=0 is decay OFF (the A/B baseline).
DECAY_S_SWEEP = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

# Decision thresholds (design choice, documented): decay is justified when it
# improves FAMA-style by >= 5pp without damaging MPA by more than 5pp.
DECAY_FAMA_GAIN_THRESHOLD = 0.05
DECAY_MPA_DAMAGE_THRESHOLD = 0.05

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"

# Corpus timestamps: old versions, new versions, distractors (newest).
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 15, tzinfo=UTC)
_T2 = datetime(2026, 1, 30, tzinfo=UTC)


@dataclass(frozen=True)
class DecaySweepPoint:
    """FAMA-style + MPA at one decay strength ``s``."""

    s: float
    fama: float
    mpa: float


@dataclass(frozen=True)
class DecayExperimentResult:
    """The decay A/B measurement (decay OFF vs ON at the best ``S``)."""

    fama_off: float  # FAMA-style with decay OFF (S=0)
    mpa_off: float  # MPA with decay OFF (S=0)
    fama_on: float  # FAMA-style with decay ON (best S)
    mpa_on: float  # MPA with decay ON (best S)
    best_s: float  # the S that maximizes FAMA-style subject to the MPA cap
    sweep: tuple[DecaySweepPoint, ...]  # per-S metrics
    n_facts: int
    n_queries: int
    regime: str  # hybrid | fallback_g2


# The knowledge-update facts: (query, old_title, old_body, new_title, new_body,
# old_fact_key, new_fact_key). The old/new versions of a fact carry DIFFERENT
# subjects (titles) so both are stored — the engine's collision detector treats
# two currently-valid episodes of the same derived subject as a collision, and
# ``remember`` never sets a ``supersedes`` chain.
_FACT_SPECS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    # Type A (decay helps): the OLD version is lexically closer to the query.
    (
        "What is the capital of France?",
        "France capital",
        "The capital of France is Paris.",
        "France capital update",
        "The capital of France is now Lyon.",
        "france-capital-old",
        "france-capital-new",
    ),
    (
        "What is the latest Python version?",
        "Python version",
        "The latest Python version is 3.11.",
        "Python version update",
        "The latest Python version is now 3.13.",
        "python-version-old",
        "python-version-new",
    ),
    (
        "Who is the CEO of Acme?",
        "Acme CEO",
        "The CEO of Acme is Jane Doe.",
        "Acme CEO update",
        "The CEO of Acme is now John Smith.",
        "acme-ceo-old",
        "acme-ceo-new",
    ),
    # Type B (decay neutral): the NEW version is lexically closer to the query.
    (
        "What is the capital of Spain?",
        "Spain capital",
        "The capital of Spain is Madrid, the largest city.",
        "Spain capital update",
        "The capital of Spain is Madrid.",
        "spain-capital-old",
        "spain-capital-new",
    ),
    (
        "What is the tallest mountain?",
        "Tallest mountain",
        "The tallest mountain is Everest, rising high above all.",
        "Tallest mountain update",
        "The tallest mountain is Everest.",
        "tallest-mountain-old",
        "tallest-mountain-new",
    ),
    # Type C (MPA fragile): the NEW version shares few tokens with the query
    # (marginally in top-k) — aggressive decay can push it out (MPA damage).
    (
        "What is the population of Tokyo?",
        "Tokyo population",
        "The population of Tokyo is 37 million.",
        "Tokyo population update",
        "Tokyo's population is now 38 million.",
        "tokyo-population-old",
        "tokyo-population-new",
    ),
    (
        "What is the area of Berlin?",
        "Berlin area",
        "The area of Berlin is 891 square kilometers.",
        "Berlin area update",
        "Berlin's area is now 892 square kilometers.",
        "berlin-area-old",
        "berlin-area-new",
    ),
)

# Background distractors (no fact structure): make the top-k selective.
_DISTRACTORS: tuple[tuple[str, str], ...] = (
    ("Quantum computing", "Qubits exploit superposition and entanglement."),
    ("Rust ownership", "Ownership moves values and borrows references."),
    ("SQLite WAL", "Write-ahead logging allows concurrent readers."),
    ("Docker networking", "Bridge networks isolate containers."),
    ("React hooks", "Hooks manage state in function components."),
    ("PostgreSQL indexes", "B-tree indexes speed up lookups."),
    ("HTTP caching", "Cache headers control response reuse."),
    ("GraphQL subscriptions", "Subscriptions push realtime updates."),
    ("Kubernetes pods", "Pods are the smallest deployable units."),
    ("Redis streams", "Streams are append-only log structures."),
    ("Terraform state", "State tracks managed infrastructure."),
    ("Nginx proxy", "Nginx reverse-proxies HTTP traffic."),
    ("WebAssembly memory", "Linear memory is the WASM heap."),
    ("TypeScript generics", "Generics parameterize types."),
    ("Git rebase", "Rebase rewrites commit history."),
    ("Elixir pipes", "Pipes thread values through function calls."),
    ("Go channels", "Channels synchronize goroutine communication."),
    ("Zig allocators", "Allocators manage memory explicitly."),
    ("Haskell monads", "Monads sequence pure computations."),
    ("Clojure atoms", "Atoms provide synchronous state updates."),
    ("Scala implicits", "Implicits resolve dependencies automatically."),
    ("OCaml modules", "Modules structure code and hide internals."),
    ("Erlang OTP", "OTP supervises fault-tolerant processes."),
    ("Julia dispatch", "Multiple dispatch selects methods by type."),
    ("Lua coroutines", "Coroutines yield control cooperatively."),
    ("Racket macros", "Macros transform code at compile time."),
    ("F sharp units", "Units of measure annotate numeric types."),
    ("Dart isolates", "Isolates run code in separate memory."),
    ("Kotlin coroutines", "Coroutines suspend without blocking threads."),
    ("Swift actors", "Actors isolate mutable state safely."),
    # Cluster distractors sharing the fact key tokens: they lower the IDF of
    # "population" so the Type C new versions sit marginally in top-k (the MPA
    # fragility is real, not a token-overlap accident).
    ("Osaka population", "The population of Osaka is 19 million."),
    ("Seoul population", "The population of Seoul is 9 million."),
)


def _make_synthetic_episodes() -> tuple[list[Episode], list[tuple[str, str, str]]]:
    """Deterministic synthetic corpus: knowledge-update facts + distractors.

    Returns ``(episodes, fact_specs)`` where ``fact_specs`` is
    ``(query, old_fact_key, new_fact_key)`` — the fact keys are carried in the
    episode provenance so the stored ``ep_id`` (engine-derived) can be mapped
    back to the fact after ingestion.
    """
    episodes: list[Episode] = []
    fact_specs: list[tuple[str, str, str]] = []

    def _ep(i: int, title: str, body: str, created_at: datetime, fact_key: str) -> Episode:
        return Episode(
            id=f"syn-{i}",
            created_at=created_at,
            schema_version="1.1",
            provenance={
                "source_type": "importer",
                "importer_vendor": "claude-mem",
                "extraction_mode": "skip",
                "session_id": "claude-mem-import-syn",
                "fact_key": fact_key,
            },
            body=f"# {title}\n\n{body}",
            title=title,
            valid_at=created_at,
            cognitive_type="semantic",
            source_type="importer",
        )

    i = 0
    for query, old_title, old_body, new_title, new_body, old_key, new_key in _FACT_SPECS:
        episodes.append(_ep(i, old_title, old_body, _T0, old_key))
        i += 1
        episodes.append(_ep(i, new_title, new_body, _T1, new_key))
        i += 1
        fact_specs.append((query, old_key, new_key))
    for title, body in _DISTRACTORS:
        episodes.append(_ep(i, title, body, _T2, f"distractor-{i}"))
        i += 1
    return episodes, fact_specs


def _ingest_episodes(
    facade: Any, episodes: list[Episode]
) -> tuple[list[Episode], dict[str, str]]:
    """Ingest episodes via the facade's ``remember`` (the single write path, skip mode).

    ``now=ep.created_at`` is passed so the engine force-sets ``created_at`` to
    the corpus timestamp (the decay bias reads the age spread). Returns the
    stored episodes (with the engine-derived ``ep_id``) and the
    ``fact_key -> ep_id`` map. Episodes rejected by a collision
    (``WriteResult.ep_id`` is None) are NOT stored and are excluded.
    """
    stored: list[Episode] = []
    ep_id_by_key: dict[str, str] = {}
    for ep in episodes:
        result = facade.remember(
            RememberPayload(
                body=ep.body or "",
                by=cast(Provenance, dict(ep.provenance)),
                valid_at=ep.valid_at,
                cognitive_type=ep.cognitive_type,
                title=ep.title,
                summary=ep.summary,
            ),
            extraction_mode="skip",
            now=ep.created_at,
        )
        if result.ep_id is None:
            continue  # COLLISION — not stored, not in the corpus
        stored.append(ep.model_copy(update={"id": result.ep_id}))
        key = ep.provenance.get("fact_key")
        if key:
            ep_id_by_key[str(key)] = result.ep_id
    return stored, ep_id_by_key


def build_synthetic_corpus(
    db_path: Path,
) -> tuple[Any, Any, list[Episode], list[tuple[str, str, str]]]:
    """Build the synthetic corpus (mechanical CI verification, no model).

    Returns ``(facade, storage, episodes, facts)`` where ``facts`` is
    ``(query, old_ep_id, new_ep_id)`` — the stored ids of the obsolete and valid
    versions of each knowledge-update fact.
    """
    episodes, fact_specs = _make_synthetic_episodes()
    facade, storage = build_facade(
        db_path,
        retrieval_available=True,
        passage_embedder=HashEmbedder(),
        # The decay re-rank over-fetches: the facade caps ``recall`` at
        # ``config.top_k``, so the config must allow the over-fetch.
        config=FacadeConfig(top_k=DECAY_OVERFETCH_K),
    )
    stored, ep_id_by_key = _ingest_episodes(facade, episodes)
    facts = [
        (query, ep_id_by_key[old_key], ep_id_by_key[new_key])
        for query, old_key, new_key in fact_specs
    ]
    return facade, storage, stored, facts


def _age_factor(created_at: datetime, now: datetime, max_age_seconds: float) -> float:
    """Normalized age in [0, 1]: the oldest episode is 1, the newest is 0."""
    age = (now - created_at).total_seconds()
    if max_age_seconds <= 0:
        return 0.0
    return age / max_age_seconds


def _apply_decay(
    rows: list[Any], s: float, now: datetime, max_age_seconds: float
) -> list[Any]:
    """Re-rank the retrieved rows by the decayed score ``score * (1 - S*age)``."""
    return sorted(
        rows,
        key=lambda r: r.score * (1.0 - s * _age_factor(r.created_at, now, max_age_seconds)),
        reverse=True,
    )


def _measure(
    facade: Any,
    facts: list[tuple[str, str, str]],
    now: datetime,
    max_age_seconds: float,
    top_k: int,
    overfetch_k: int,
) -> tuple[tuple[DecaySweepPoint, ...], str]:
    """Run the FAMA-style + MPA measurement over the S sweep.

    Returns ``(sweep, regime)``. For each fact query the retrieval over-fetches
    to ``overfetch_k``, the decay bias re-ranks + truncates to ``top_k``, and
    FAMA-style (old version NOT in top-k) and MPA (new version in top-k) are
    accumulated per ``S``.
    """
    regime = "hybrid"
    fama_counts = [0.0] * len(DECAY_S_SWEEP)
    mpa_counts = [0.0] * len(DECAY_S_SWEEP)
    n_queries = 0
    if not facts:
        # No fact queries — nothing to measure (all-zero sweep, honest).
        return (
            tuple(DecaySweepPoint(s=s, fama=0.0, mpa=0.0) for s in DECAY_S_SWEEP),
            regime,
        )
    for query, old_id, new_id in facts:
        rows = facade.recall(query, k=overfetch_k)
        if rows and all(r.score == 0.0 for r in rows):
            regime = _FALLBACK_G2
        n_queries += 1
        for si, s in enumerate(DECAY_S_SWEEP):
            decayed = _apply_decay(rows, s, now, max_age_seconds)[:top_k]
            ids = [r.ep_id for r in decayed]
            if old_id not in ids:
                fama_counts[si] += 1.0
            if new_id in ids:
                mpa_counts[si] += 1.0
    sweep = tuple(
        DecaySweepPoint(s=s, fama=c / n_queries, mpa=m / n_queries)
        for s, c, m in zip(DECAY_S_SWEEP, fama_counts, mpa_counts, strict=True)
    )
    return sweep, regime


def _select_best_s(sweep: tuple[DecaySweepPoint, ...], mpa_off: float) -> float:
    """The S that maximizes FAMA-style subject to the MPA damage cap.

    Ties on FAMA-style resolve to the smallest S (least aggressive decay).
    ``S=0`` (decay OFF) always satisfies the cap (damage 0), so the fallback is
    never empty — when no decay strength is acceptable, the honest best S is 0.
    """
    candidates = [
        p for p in sweep if (mpa_off - p.mpa) <= DECAY_MPA_DAMAGE_THRESHOLD
    ]
    if not candidates:
        return 0.0
    return max(candidates, key=lambda p: (p.fama, -p.s)).s


def build_real_corpus(
    db_path: Path,
) -> tuple[Any, Any, list[Episode], list[tuple[str, str, str]]]:
    """Build the real LMEB-S knowledge-update corpus (the authoritative decision).

    Not yet built — fail-loud (the synthetic corpus verifies the harness
    MECHANICS; the authoritative Sprint D decision comes from an LMEB-S
    knowledge-update run).
    """
    raise NotImplementedError(
        "the LMEB-S knowledge-update decay corpus is not built yet; "
        "run with corpus='synthetic' (mechanical verification only)"
    )


def run_decay_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = DECAY_TOP_K,
    overfetch_k: int = DECAY_OVERFETCH_K,
) -> DecayExperimentResult:
    """Run the decay A/B measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — not yet built). ``db_path`` defaults to a
    fresh temp DB (reproducible).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(mkdtemp_scoped("seahorse-decay-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes, facts = build_synthetic_corpus(db)
    else:
        facade, storage, episodes, facts = build_real_corpus(db)
    now = max(ep.created_at for ep in episodes)
    max_age_seconds = max(
        (now - ep.created_at).total_seconds() for ep in episodes
    )
    try:
        sweep, regime = _measure(facade, facts, now, max_age_seconds, top_k, overfetch_k)
    finally:
        storage.close()
    fama_off = sweep[0].fama  # S=0 is the decay-OFF baseline
    mpa_off = sweep[0].mpa
    best_s = _select_best_s(sweep, mpa_off)
    best = next(p for p in sweep if p.s == best_s)
    return DecayExperimentResult(
        fama_off=fama_off,
        mpa_off=mpa_off,
        fama_on=best.fama,
        mpa_on=best.mpa,
        best_s=best_s,
        sweep=sweep,
        n_facts=len(facts),
        n_queries=len(facts),
        regime=regime,
    )


def decide_decay(result: DecayExperimentResult) -> dict:
    """Apply the decision: Sprint D (decay ranking bias) vs keep decay OFF.

    Returns a decision dict (``decision``, ``flip``, ``reason``, ``fama_off``,
    ``mpa_off``, ``fama_on``, ``mpa_on``, ``best_s``). Invalid (no decision)
    when the run degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the decay A/B is not meaningful — re-run with the embeddings extra"
            ),
            "fama_off": result.fama_off,
            "mpa_off": result.mpa_off,
            "fama_on": result.fama_on,
            "mpa_on": result.mpa_on,
            "best_s": result.best_s,
        }
    fama_gain = result.fama_on - result.fama_off
    mpa_damage = result.mpa_off - result.mpa_on
    if (
        fama_gain >= DECAY_FAMA_GAIN_THRESHOLD
        and mpa_damage <= DECAY_MPA_DAMAGE_THRESHOLD
    ):
        return {
            "decision": "sprint_d",
            "flip": True,
            "reason": (
                f"decay (best S={result.best_s:g}) improves FAMA-style by "
                f"{fama_gain:.3f} (>= {DECAY_FAMA_GAIN_THRESHOLD:.2f}) without "
                f"damaging MPA by more than {mpa_damage:.3f} "
                f"(<= {DECAY_MPA_DAMAGE_THRESHOLD:.2f}) — Sprint D (decay ranking "
                f"bias) is justified"
            ),
            "fama_off": result.fama_off,
            "mpa_off": result.mpa_off,
            "fama_on": result.fama_on,
            "mpa_on": result.mpa_on,
            "best_s": result.best_s,
        }
    return {
        "decision": "decay_off",
        "flip": False,
        "reason": (
            f"decay (best S={result.best_s:g}) does not improve FAMA-style by "
            f">= {DECAY_FAMA_GAIN_THRESHOLD:.2f} (gain {fama_gain:.3f}) or damages "
            f"MPA by > {DECAY_MPA_DAMAGE_THRESHOLD:.2f} (damage {mpa_damage:.3f}) "
            f"— keep decay OFF (default, opt-in)"
        ),
        "fama_off": result.fama_off,
        "mpa_off": result.mpa_off,
        "fama_on": result.fama_on,
        "mpa_on": result.mpa_on,
        "best_s": result.best_s,
    }


def render_decay_report(result: DecayExperimentResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Decay experiment: FAMA-style (decide Sprint D)",
        "",
        f"regime: {result.regime}",
        f"knowledge-update facts: {result.n_facts}",
        f"fact queries: {result.n_queries}",
        f"top-k: {DECAY_TOP_K} (over-fetch {DECAY_OVERFETCH_K})",
        "",
        "FAMA-style (obsolete old version NOT in top-k) / MPA (valid new version in top-k):",
    ]
    for p in result.sweep:
        marker = "  <- best S" if p.s == result.best_s else ""
        lines.append(f"  S={p.s:g}  FAMA={p.fama:.3f}  MPA={p.mpa:.3f}{marker}")
    lines.append("")
    lines.append("## A/B (decay OFF vs ON at best S)")
    gain = result.fama_on - result.fama_off
    damage = result.mpa_off - result.mpa_on
    lines.append(
        f"FAMA-style: {result.fama_off:.3f} -> {result.fama_on:.3f} "
        f"(gain {gain:+.3f})"
    )
    lines.append(
        f"MPA:        {result.mpa_off:.3f} -> {result.mpa_on:.3f} "
        f"(damage {damage:+.3f})"
    )
    lines.append("")
    lines.append("## Decision")
    lines.append(f"decision: {decision.get('decision')}")
    lines.append(f"flip: {decision.get('flip')}")
    lines.append(f"reason: {decision.get('reason', '')}")
    return "\n".join(lines)


__all__ = [
    "DECAY_FAMA_GAIN_THRESHOLD",
    "DECAY_MPA_DAMAGE_THRESHOLD",
    "DECAY_OVERFETCH_K",
    "DECAY_S_SWEEP",
    "DECAY_TOP_K",
    "DecayExperimentResult",
    "DecaySweepPoint",
    "build_real_corpus",
    "build_synthetic_corpus",
    "decide_decay",
    "render_decay_report",
    "run_decay_experiment",
]
