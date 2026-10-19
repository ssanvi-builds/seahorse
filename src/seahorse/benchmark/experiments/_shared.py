"""Shared internals for the benchmark experiment modules (private).

The experiment modules once repeated a handful of fixture/ingest/measurement
helpers byte-for-byte (the doubles must stay deterministic and identical across
experiments, so the copies were deliberate until they could be unified — the
episode ingest loops are now ONE ``ingest_episodes``). This
module is the single benchmark-internal home for those helpers — it imports
ONLY the contracts/facade types (no experiment module), so it stays dependency-
free at import time (the heavy backends stay lazy inside the functions).

Deliberately NOT shared with the core (benchmark-internal by design):

- ``first_sentence`` is NOT ``seahorse.write_path.extract._first_sentence``:
  the core version splits on ``.``/``!``/``?`` + whitespace/end; this one
  splits on the first ``.`` only. Different sentence-boundary rules →
  different synthetic summaries → keep them separate.
- ``subject_of`` mirrors the core's title>H1 rule but falls back to the
  stripped body (or ``""``) instead of raising ``SubjectDerivationError``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from seahorse.contracts.episode import Episode
from seahorse.facade.errors import PitRecallNotSupportedMVP0
from seahorse.facade.types import Provenance, RememberPayload

if TYPE_CHECKING:
    from seahorse.benchmark.experiments.end_to_end import EndToEndQuestion

# The fixed wall-clock anchor for the synthetic Episode fixtures (any value
# works — the fixtures never assert on it — but it must be ONE value so the
# corpora stay reproducible).
BENCH_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

# The sync embedding seam shared by the within-session re-rank experiments.
Embedder = Callable[[Sequence[str], str], Sequence[Sequence[float]]]

# The honest detected regime that invalidates a hybrid-regime experiment.
FALLBACK_G2 = "fallback_g2"


def first_sentence(text: str) -> str:
    """The first sentence of a body (the ``deterministic_extract`` summary)."""
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped.split(".", 1)[0] + "." if "." in stripped else stripped


def stub_episode(ep_id: str, body: str) -> Episode:
    """A lightweight Episode for the locator (only ``id`` + ``body`` are used)."""
    return Episode(
        id=ep_id,
        created_at=BENCH_EPOCH,
        schema_version="1.1",
        provenance={"source_type": "agent", "session_id": ""},
        body=body,
        valid_at=BENCH_EPOCH,
    )


@dataclass(frozen=True)
class EpisodeIngestResult:
    """One skip-mode episode ingest, everything the consumers reduce from it.

    ``stored`` keeps only what the engine accepted (collisions — ``WriteResult.ep_id``
    None — are excluded); ``id_map`` maps the ORIGINAL ``Episode.id`` to the
    STORED ep_id (the engine derives a deterministic UUIDv5 for importer source,
    which may differ); ``ep_id_to_session``/``fact_key_to_ep_id`` resolve from
    provenance.
    """

    stored: list[Episode]
    id_map: dict[str, str]
    ep_id_to_session: dict[str, str]
    fact_key_to_ep_id: dict[str, str]


def ingest_episodes(
    facade: Any,
    episodes: Sequence[Episode],
    *,
    now_of: Callable[[Episode], datetime] | None = None,
) -> EpisodeIngestResult:
    """The single Episode-shape remember loop (skip mode) for the experiments.

    ``now_of`` resolves each remember's ``now`` (decay forces ``created_at`` so
    its bias reads the fixture's own timestamps; everybody else leaves the
    facade clock). ``fact_key_to_ep_id`` is FIRST-wins (``setdefault`` — SeahorseSUT
    parity; decay's per-version keys are unique per corpus, so its local
    last-wins loop was equivalent). Empty-string/absent fact keys are skipped.
    """
    stored: list[Episode] = []
    id_map: dict[str, str] = {}
    ep_id_to_session: dict[str, str] = {}
    fact_key_to_ep_id: dict[str, str] = {}
    for ep in episodes:
        payload = RememberPayload(
            body=ep.body or "",
            by=cast(Provenance, dict(ep.provenance)),
            valid_at=ep.valid_at,
            cognitive_type=ep.cognitive_type,
            title=ep.title,
            summary=ep.summary,
        )
        result = facade.remember(
            payload, extraction_mode="skip", now=now_of(ep) if now_of else None
        )
        if result.ep_id is None:
            continue  # COLLISION — not stored, not in the corpus
        stored.append(ep.model_copy(update={"id": result.ep_id}))
        id_map[ep.id] = result.ep_id
        ep_id_to_session[result.ep_id] = ep.provenance.get("session_id", "")
        key = ep.provenance.get("fact_key")
        if key:
            fact_key_to_ep_id.setdefault(str(key), result.ep_id)
    return EpisodeIngestResult(
        stored=stored,
        id_map=id_map,
        ep_id_to_session=ep_id_to_session,
        fact_key_to_ep_id=fact_key_to_ep_id,
    )


def session_to_ep_ids(ep_id_to_session: Mapping[str, str]) -> dict[str, list[str]]:
    """The session→[stored ep_ids] inverse, ep order preserved inside each
    session (the three within-session experiments' byte-identical reduction)."""
    inverse: dict[str, list[str]] = {}
    for ep_id, sid in ep_id_to_session.items():
        inverse.setdefault(sid, []).append(ep_id)
    return inverse


def golden_session_ep_ids(
    golden_session_ids: Sequence[str], session_to_ep_ids: dict[str, list[str]]
) -> list[str]:
    """The stored episode ids of a question's golden sessions (deduped)."""
    seen: list[str] = []
    for sid in golden_session_ids:
        for ep_id in session_to_ep_ids.get(sid, []):
            if ep_id not in seen:
                seen.append(ep_id)
    return seen


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity with a zero-norm guard (a zero vector scores 0.0)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def default_embedder(corpus: str) -> Embedder:
    """The sync embedding seam for within-session ranking.

    Synthetic → the deterministic ``HashEmbedder`` (CI); lmeb-s → the real
    fastembed backend (the authoritative run). Returns a sync callable
    ``(texts, role) -> list[vectors]``. The backend imports stay lazy inside so
    importing this module stays dependency-free.
    """
    model: Any
    if corpus == "synthetic":
        from seahorse.benchmark.experiments.synthetic import HashEmbedder  # lazy

        model = HashEmbedder()
    else:
        from seahorse.embeddings.fastembed_backend import build_fastembed_embedder  # lazy

        model = build_fastembed_embedder()
    from seahorse.embeddings.query_adapter import run_coroutine  # lazy

    def _embed(texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        vecs = run_coroutine(model.embed(texts, role))
        return [list(row) for row in vecs]

    return _embed


def recall_rows(
    facade: Any, q: EndToEndQuestion, top_k: int, *, session_boost: bool = False
):
    """Recall the top-k rows (active-now, the honest PIT fallback mirroring
    ``measure_end_to_end`` — a regime without a PIT axis raises
    ``PitRecallNotSupportedMVP0`` → active-now, never crash the run).

    ``session_boost`` is pinned ``False`` by default (= the engine default,
    ``facade.recall``) because every caller measures a pure-RRF baseline or an
    upper bound that the engine's automatic session boost would corrupt (the
    boost re-ranks the top session, changing which sessions surface in the
    top-k). The engine's automatic version is verified separately.
    """
    if q.question_date is not None:
        from seahorse.disclosure.types import PITPoint  # lazy

        try:
            return facade.recall(
                q.query,
                k=top_k,
                pit=PITPoint(kind="state_at", t=q.question_date),
                session_boost=session_boost,
            )
        except PitRecallNotSupportedMVP0:
            return facade.recall(q.query, k=top_k, session_boost=session_boost)
    return facade.recall(q.query, k=top_k, session_boost=session_boost)


def normalize_tokens(text: str) -> list[str]:
    """Lower-case + strip non-alphanumeric tokens.

    All three deterministic doubles intentionally share this one tokenizer —
    the ``HashReranker`` (stage-3 reorder), the ``ExtractiveReader`` (reader
    token overlap), and the BM25 approximation in the two-stage re-rank. The
    doubles measure today's behavior, so a shared helper cannot drift from what
    they measure; a future double needing a different tokenizer defines its own
    locally again.
    """
    return [t for t in re.sub(r"[^a-z0-9 ]", "", text.lower()).split() if t]


def is_fallback_regime(rows: Sequence[Any]) -> bool:
    """Whether a recall result degrades the experiment to the ``fallback_g2``
    regime: non-empty rows whose scores are ALL zero (the hybrid path was not
    wired — the retrieval fell back to the listing regime)."""
    return bool(rows) and all(r.score == 0.0 for r in rows)


def mean_or_zero(values: Sequence[float]) -> float:
    """The arithmetic mean, or 0.0 for an empty sequence (the rate helper)."""
    return sum(values) / len(values) if values else 0.0


def subject_of(ep: Episode) -> str:
    """The episode's subject: the H1 of the body (the importer guarantees it)."""
    if ep.title:
        return ep.title
    body = ep.body or ""
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return body.strip() or ""