"""Tests for the output cache (f5-16 §5.5)."""

from __future__ import annotations

from seahorse.benchmark.reproducibility.cache import OutputCache


def test_cache_put_get(tmp_path):
    cache = OutputCache(tmp_path / "cache.db")
    cache.put("k1", "v1")
    assert cache.get("k1") == "v1"
    cache.close()


def test_cache_missing_returns_none(tmp_path):
    cache = OutputCache(tmp_path / "cache.db")
    assert cache.get("missing") is None
    cache.close()


def test_cache_key_is_deterministic():
    a = OutputCache.key("run1", "prompt1", "params1")
    b = OutputCache.key("run1", "prompt1", "params1")
    assert a == b
    assert len(a) == 64


def test_cache_key_changes_with_prompt():
    a = OutputCache.key("run1", "prompt1", "params1")
    b = OutputCache.key("run1", "prompt2", "params1")
    assert a != b


def test_cache_put_overwrites(tmp_path):
    cache = OutputCache(tmp_path / "cache.db")
    cache.put("k1", "v1")
    cache.put("k1", "v2")
    assert cache.get("k1") == "v2"
    cache.close()
