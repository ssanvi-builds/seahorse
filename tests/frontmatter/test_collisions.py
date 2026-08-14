"""Collision detection unit tests."""

from __future__ import annotations

from seahorse.frontmatter.collisions import (
    COLLISION_MAP,
    X_RESERVED_KEYS,
    detect_legacy_collisions,
    detect_x_reserved_collision,
)


class TestDetectLegacyCollisions:
    def test_created_maps_to_created_at(self) -> None:
        assert detect_legacy_collisions({"created"}) == ["created vs created_at"]

    def test_modified_and_publish_both_reported_sorted(self) -> None:
        result = detect_legacy_collisions({"modified", "publish"})
        assert result == ["modified vs modified_at", "publish vs publish_state"]

    def test_no_collision_when_no_legacy_keys_match(self) -> None:
        assert detect_legacy_collisions({"tags", "title", "aliases"}) == []

    def test_empty_keys_returns_empty(self) -> None:
        assert detect_legacy_collisions(set()) == []

    def test_mixed_legacy_and_unknown_keys(self) -> None:
        # Unknown keys are ignored; only legacy counterparts reported.
        assert detect_legacy_collisions({"tags", "created", "cssclasses"}) == [
            "created vs created_at"
        ]

    def test_collision_map_covers_known_legacy_keys(self) -> None:
        # Guard against accidental map shrinkage.
        assert set(COLLISION_MAP.keys()) == {"created", "modified", "publish"}


class TestDetectXReservedCollision:
    def test_x_valid_at_is_reserved(self) -> None:
        assert detect_x_reserved_collision({"x-valid-at"}) == "x-valid-at"

    def test_x_seahorse_id_is_reserved(self) -> None:
        assert detect_x_reserved_collision({"x-seahorse-id", "tags"}) == "x-seahorse-id"

    def test_no_collision_returns_none(self) -> None:
        assert detect_x_reserved_collision({"tags", "created"}) is None

    def test_empty_keys_returns_none(self) -> None:
        assert detect_x_reserved_collision(set()) is None

    def test_all_reserved_keys_recognized(self) -> None:
        for key in X_RESERVED_KEYS:
            assert detect_x_reserved_collision({key}) == key