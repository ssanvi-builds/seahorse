"""Answer-bearing episode localization heuristic (pure utility).

LMEB-S golden answers live in *sessions*: the raw rows carry only
``question``/``answer``/``answer_session_ids``, and each turn has just
``content``+``role`` — there is **no answer→turn mapping**. To measure
episode-level retrieval recall we must first decide *which episode(s) of the
golden session contain the answer*. This module implements that heuristic as a
pure function over the session's episodes.

Scoring
-------
The score for an episode is the **longest contiguous n-gram** of normalized
answer tokens present in the episode body (order- and contiguity-sensitive).
The status reflects the confidence of the localization:

- ``verbatim``        — the full answer appears contiguously (n-gram == length)
- ``fragment``        — a distinctive contiguous fragment (n >= 2) is present
- ``single_token``    — only a one-token overlap was found
- ``unlocalized``     — no answer token is present in any episode
- ``embedding_fallback`` — an injected embedder picked the closest episode when
  no token match was found (last resort, see caveats)

When several episodes reach the best level (e.g. the user turn stating the fact
and the assistant's echo both contain the answer), **all** of them are returned:
either one in the top-k counts as a hit.

Authoritative distribution over the reproducible 100-question subsample (seed
42, split ``c6178fd0a436``): 50 verbatim / 34 fragment / 9 single-token / 7
unlocalized. The 7 unlocalized answers are **derived** (computed numbers such as
``"43 years older"`` assembled from two facts); no text-matching heuristic can
localize them and the harness must NOT present them as ground truth.

Caveats
-------
- This is a heuristic, never dataset ground truth. Answers in LMEB-T are
  paraphrases; ``fragment`` matches are the typical case and a partial n-gram
  does not guarantee the episode *supports* the answer.
- The ``embedder`` fallback is injectable and purely optional: a pure callable
  mapping an *episode id* (and the golden answer text) to an embedding vector.
  It is used only when no token match exists, and is reported under
  ``embedding_fallback`` so downstream consumers can caveat it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from seahorse.contracts.episode import Episode

Embedder = Callable[[str], Sequence[float]]

STATUS_VERBATIM = "verbatim"
STATUS_FRAGMENT = "fragment"
STATUS_SINGLE_TOKEN = "single_token"
STATUS_UNLOCALIZED = "unlocalized"
STATUS_EMBEDDING_FALLBACK = "embedding_fallback"


@dataclass(frozen=True)
class LocalizationResult:
    """Where the golden answer lives, per the heuristic."""

    answer_ep_ids: tuple[str, ...]
    status: str
    best_ngram: int
    n_episodes: int


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation (keeps word boundaries)."""
    return re.sub(r"[^\w\s]", " ", text.lower())


def _longest_contiguous_ngram(answer_tokens: Sequence[str], body_tokens: Sequence[str]) -> int:
    """Longest n such that a contiguous window of ``answer_tokens`` of length n
    appears contiguously in ``body_tokens`` (same order)."""
    if not answer_tokens or not body_tokens:
        return 0
    max_len = len(answer_tokens)
    for n in range(max_len, 0, -1):
        answer_windows = {
            tuple(answer_tokens[i : i + n]) for i in range(max_len - n + 1)
        }
        for j in range(len(body_tokens) - n + 1):
            if tuple(body_tokens[j : j + n]) in answer_windows:
                return n
    return 0


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def locate_answer_episodes(
    golden_answer: str,
    episodes: Sequence[Episode],
    *,
    embedder: Embedder | None = None,
) -> LocalizationResult:
    """Localize the episode(s) of a golden session that contain the answer.

    ``episodes`` are the golden session's episodes (turns). Returns a
    ``LocalizationResult`` with every episode id that reaches the best match
    level, a status, and the best contiguous n-gram found. The embedding
    fallback is only consulted when no token match exists at all.
    """
    answer_tokens = _normalize(golden_answer).split()
    n_episodes = len(episodes)

    if not answer_tokens or not episodes:
        return LocalizationResult((), STATUS_UNLOCALIZED, 0, n_episodes)

    per_episode: dict[str, tuple[int, int]] = {}  # ep_id -> (best_ngram, overlap)
    for ep in episodes:
        body_tokens = _normalize(ep.body or "").split()
        best = _longest_contiguous_ngram(answer_tokens, body_tokens)
        overlap = len(set(answer_tokens) & set(body_tokens))
        per_episode[ep.id] = (best, overlap)

    best_ngram = max((v[0] for v in per_episode.values()), default=0)

    if best_ngram == len(answer_tokens) and best_ngram >= 2:
        status = STATUS_VERBATIM
    elif best_ngram >= 2:
        status = STATUS_FRAGMENT
    elif best_ngram == 1:
        status = STATUS_SINGLE_TOKEN
    else:
        if embedder is None:
            return LocalizationResult((), STATUS_UNLOCALIZED, 0, n_episodes)
        answer_vec = embedder(golden_answer)
        best_id = max(
            episodes,
            key=lambda ep: _cosine(answer_vec, embedder(ep.id)),
        ).id
        return LocalizationResult((best_id,), STATUS_EMBEDDING_FALLBACK, 0, n_episodes)

    matching = tuple(ep_id for ep_id, (n, _) in per_episode.items() if n == best_ngram)
    return LocalizationResult(matching, status, best_ngram, n_episodes)


def answer_fragment_present(golden_answer: str, text: str, *, min_ngram: int = 2) -> bool:
    """Whether a distinctive contiguous fragment (>= ``min_ngram`` tokens) of the
    answer appears in ``text`` (case/punctuation-insensitive).

    The diagnostic bridge between episode recall and end-to-end accuracy: does a
    fragment of the answer even reach the reader's context? Paraphrase answers
    mean exact matching underestimates — the caller must caveat that.
    """
    answer_tokens = _normalize(golden_answer).split()
    text_tokens = _normalize(text).split()
    return _longest_contiguous_ngram(answer_tokens, text_tokens) >= min_ngram
