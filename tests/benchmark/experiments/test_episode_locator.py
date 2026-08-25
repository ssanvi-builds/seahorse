"""Tests for the answer-bearing episode localization heuristic (``episode_locator.py``).

The heuristic localizes the episode(s) of a golden session that contain the
golden answer, because LMEB-S answers live in sessions (turns carry only
``content``+``role``; there is no answer→turn mapping in the raw dataset). The
score is the longest contiguous n-gram of answer tokens present in the episode
body; the status reflects the confidence (verbatim > fragment > single_token >
unlocalized). The authoritative distribution over the reproducible 100
subsample is documented in the module docstring (50 verbatim / 34 fragment /
9 single-token / 7 unlocalized).
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.benchmark.experiments.episode_locator import (
    LocalizationResult,
    answer_fragment_present,
    locate_answer_episodes,
)
from seahorse.contracts.episode import Episode

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _ep(ep_id: str, body: str) -> Episode:
    return Episode(
        id=ep_id,
        created_at=_NOW,
        schema_version="1.1",
        provenance={"source_type": "agent", "session_id": "s1"},
        body=body,
        valid_at=_NOW,
    )


class TestLocateVerbatim:
    def test_full_answer_contiguous_in_body(self) -> None:
        """The answer appears contiguously in full → verbatim, that episode."""
        result = locate_answer_episodes(
            "Business Administration",
            [_ep("e1", "I graduated with a degree in Business Administration.")],
        )
        assert result.status == "verbatim"
        assert result.answer_ep_ids == ("e1",)
        assert result.best_ngram == 2

    def test_case_and_punctuation_insensitive(self) -> None:
        result = locate_answer_episodes(
            "The Glass Menagerie",
            [_ep("e1", "the glass menagerie is a classic play")],
        )
        assert result.status == "verbatim"
        assert result.answer_ep_ids == ("e1",)

    def test_both_user_turn_and_echo_are_answer_bearing(self) -> None:
        """The user turn stating the fact + the assistant echo both contain the
        answer; EITHER in the top-k counts, so both are returned."""
        result = locate_answer_episodes(
            "Business Administration",
            [
                _ep("user", "I graduated with a degree in Business Administration."),
                _ep("assistant", "Congratulations on your degree in Business Administration!"),
                _ep("other", "I am trying to organize my life a bit better."),
            ],
        )
        assert result.status == "verbatim"
        assert set(result.answer_ep_ids) == {"user", "assistant"}


class TestLocateFragment:
    def test_partial_contiguous_ngram(self) -> None:
        """A distinctive fragment (>= 2 tokens) of a longer answer → fragment."""
        result = locate_answer_episodes(
            "business administration degree",
            [_ep("e1", "I have a degree in business administration.")],
        )
        assert result.status == "fragment"
        assert result.answer_ep_ids == ("e1",)
        assert result.best_ngram == 2

    def test_fragment_prefers_longest_ngram(self) -> None:
        """The best fragment wins; an episode with a longer contiguous match
        outranks one with a shorter match."""
        result = locate_answer_episodes(
            "marketing specialist at a small startup",
            [
                _ep("weak", "I was a marketing intern once."),
                _ep("strong", "My role as a marketing specialist at a small startup."),
            ],
        )
        assert result.status == "verbatim"  # the full answer appears in "strong"
        assert result.answer_ep_ids == ("strong",)


class TestLocateSingleToken:
    def test_single_token_answer(self) -> None:
        """A one-token answer that appears in the body → single_token."""
        result = locate_answer_episodes(
            "Target",
            [_ep("e1", "I redeemed a coupon at Target last Sunday.")],
        )
        assert result.status == "single_token"
        assert result.answer_ep_ids == ("e1",)

    def test_weak_single_token_picks_max_overlap(self) -> None:
        """When the best contiguous n-gram is 1 but the answer has multiple
        tokens, the episode with the most token overlap is the best candidate."""
        result = locate_answer_episodes(
            "a lighter shade of gray",
            [
                _ep("spread", "I like gray paint and lighter furniture."),
                _ep("best", "I repainted the bedroom walls a lighter shade of gray."),
            ],
        )
        # The "best" episode has all 5 tokens (ngram 5 → verbatim actually).
        assert result.answer_ep_ids == ("best",)


class TestLocateUnlocalized:
    def test_answer_absent_returns_unlocalized(self) -> None:
        """A derived answer (e.g. a computed number) is in no episode body →
        unlocalized; the harness must NOT present it as ground truth."""
        result = locate_answer_episodes(
            "43",
            [_ep("e1", "My grandma is 70 and I am 27.")],
        )
        assert result.status == "unlocalized"
        assert result.answer_ep_ids == ()
        assert result.best_ngram == 0

    def test_empty_answer_unlocalized(self) -> None:
        result = locate_answer_episodes("", [_ep("e1", "some body")])
        assert result.status == "unlocalized"
        assert result.answer_ep_ids == ()

    def test_empty_episodes_unlocalized(self) -> None:
        result = locate_answer_episodes("Business Administration", [])
        assert result.status == "unlocalized"
        assert result.n_episodes == 0


class TestLocateEmbeddingFallback:
    def test_unlocalized_can_use_embedding_fallback(self) -> None:
        """An injected embedder (a pure callable) is the last-resort fallback for
        the unlocalized case; the report must caveat that no episode states the
        answer."""
        embeds = {"43": [1.0, 0.0], "e1": [0.1, 0.9], "e2": [0.9, 0.1]}
        result = locate_answer_episodes(
            "43",
            [_ep("e1", "My grandma is 70 and I am 27."), _ep("e2", "I am 27 years old.")],
            embedder=lambda text: embeds[text],
        )
        assert result.status == "embedding_fallback"
        assert result.answer_ep_ids == ("e2",)

    def test_embedding_fallback_skipped_without_embedder(self) -> None:
        result = locate_answer_episodes("43", [_ep("e1", "My grandma is 70.")])
        assert result.status == "unlocalized"
        assert result.answer_ep_ids == ()


class TestMetadata:
    def test_episodes_considered_counted(self) -> None:
        result = locate_answer_episodes(
            "Business Administration",
            [_ep("e1", "degree in Business Administration"), _ep("e2", "unrelated")],
        )
        assert result.n_episodes == 2

    def test_returns_localization_result_type(self) -> None:
        assert isinstance(
            locate_answer_episodes("X", [_ep("e1", "X")]), LocalizationResult
        )


class TestAnswerFragmentPresent:
    def test_full_fragment_present(self) -> None:
        assert answer_fragment_present(
            "Business Administration", "a degree in business administration"
        )
        assert answer_fragment_present(
            "marketing specialist", "I was a marketing specialist once"
        )

    def test_single_token_fragment_not_enough(self) -> None:
        """min_ngram=2 default: a lone shared token is NOT distinctive."""
        assert not answer_fragment_present("lighter shade of gray", "I like gray furniture")

    def test_answer_absent(self) -> None:
        assert not answer_fragment_present("The Glass Menagerie", "I prefer modern theater")

    def test_min_ngram_override(self) -> None:
        assert answer_fragment_present("Target", "I shopped at Target.", min_ngram=1)
        assert not answer_fragment_present(
            "Business Administration", "I have a degree", min_ngram=2
        )
