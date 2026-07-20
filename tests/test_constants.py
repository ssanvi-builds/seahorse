"""Tests for the shared constants module (pre-work for #13/#14)."""

from __future__ import annotations

import pytest

from seahorse import constants
from seahorse.constants import (
    BODY_MAX_CHARS,
    COGNITIVE_TYPES,
    EP_ID_MAX_CHARS,
    PROVENANCE_ID_MAX_CHARS,
    PROVENANCE_SHORT_MAX_CHARS,
    QUERY_MAX_CHARS,
    REASON_MAX_CHARS,
    SOURCE_TYPES,
    SUBJECT_FILTER_MAX_CHARS,
    TAG_MAX_CHARS,
    TAGS_MAX_ITEMS,
)


class TestCognitiveTypes:
    def test_has_the_four_active_f31_values(self) -> None:
        active = {"episodic", "semantic", "social", "project_doc"}
        assert active.issubset(COGNITIVE_TYPES)

    def test_has_the_two_reserved_f31_values(self) -> None:
        reserved = {"procedural", "working"}
        assert reserved.issubset(COGNITIVE_TYPES)

    def test_exactly_six_f31_values(self) -> None:
        assert len(COGNITIVE_TYPES) == 6

    def test_does_not_contain_preference(self) -> None:
        # f5-05 §5.6 had a divergent `preference` value that does not exist in F3.1.
        assert "preference" not in COGNITIVE_TYPES

    def test_is_frozenset(self) -> None:
        assert isinstance(COGNITIVE_TYPES, frozenset)


class TestSourceTypes:
    def test_has_the_four_values(self) -> None:
        assert set(SOURCE_TYPES) == {"agent", "human", "importer", "system"}

    def test_is_frozenset(self) -> None:
        assert isinstance(SOURCE_TYPES, frozenset)


class TestWireCaps:
    def test_body_cap(self) -> None:
        assert BODY_MAX_CHARS == 32_768

    def test_query_cap(self) -> None:
        assert QUERY_MAX_CHARS == 2_048

    def test_reason_cap(self) -> None:
        assert REASON_MAX_CHARS == 512

    def test_tags_cap(self) -> None:
        assert TAGS_MAX_ITEMS == 32

    def test_tag_item_cap(self) -> None:
        assert TAG_MAX_CHARS == 256

    def test_ep_id_cap(self) -> None:
        assert EP_ID_MAX_CHARS == 64

    def test_provenance_caps(self) -> None:
        assert PROVENANCE_ID_MAX_CHARS == 256
        assert PROVENANCE_SHORT_MAX_CHARS == 128

    def test_subject_filter_reuses_query_budget(self) -> None:
        assert SUBJECT_FILTER_MAX_CHARS == QUERY_MAX_CHARS

    def test_all_caps_are_ints(self) -> None:
        for cap in (
            BODY_MAX_CHARS,
            QUERY_MAX_CHARS,
            REASON_MAX_CHARS,
            TAGS_MAX_ITEMS,
            TAG_MAX_CHARS,
            EP_ID_MAX_CHARS,
            PROVENANCE_ID_MAX_CHARS,
            PROVENANCE_SHORT_MAX_CHARS,
            SUBJECT_FILTER_MAX_CHARS,
        ):
            assert isinstance(cap, int)
            assert cap > 0


class TestReExportFromFacadeTypes:
    def test_facade_types_re_exports_cognitive_types(self) -> None:
        from seahorse.facade.types import COGNITIVE_TYPES as facade_cog

        assert facade_cog is COGNITIVE_TYPES  # same object, single source

    def test_facade_types_re_exports_source_types(self) -> None:
        from seahorse.facade.types import SOURCE_TYPES as facade_src

        assert facade_src is SOURCE_TYPES

    def test_facade_package_re_exports_cognitive_types(self) -> None:
        from seahorse.facade import COGNITIVE_TYPES as pkg_cog

        assert pkg_cog is COGNITIVE_TYPES


class TestImmutability:
    def test_cannot_mutate_cognitive_types(self) -> None:
        with pytest.raises(AttributeError):
            COGNITIVE_TYPES.add("fact")  # type: ignore[attr-defined]

    def test_module_all_listed(self) -> None:
        expected = {
            "COGNITIVE_TYPES",
            "SOURCE_TYPES",
            "BODY_MAX_CHARS",
            "QUERY_MAX_CHARS",
            "REASON_MAX_CHARS",
            "TAGS_MAX_ITEMS",
            "TAG_MAX_CHARS",
            "EP_ID_MAX_CHARS",
            "PROVENANCE_ID_MAX_CHARS",
            "PROVENANCE_SHORT_MAX_CHARS",
            "SUBJECT_FILTER_MAX_CHARS",
        }
        assert set(constants.__all__) == expected