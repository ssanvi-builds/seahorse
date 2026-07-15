"""Validate the #5 skip-path border contract (Phase 11, owned #2).

``is_valid_skip_path`` is the pure border validator between the Engine (#2)
and the Extractor (#5): a payload whose ``provenance.extraction_mode == "skip"``
is routable down the deterministic skip-path ONLY if it satisfies the
contract (4 timestamps in the right shape + ``valid_at <= created_at`` + a
semver ``schema_version``).

Reconciliation with the ``-> bool`` signature (f5-02 §6.4, l.745-758):

- ``extraction_mode != "skip"`` -> ``False`` (not a skip-path payload; #5 uses
  another path — not an error).
- ``extraction_mode == "skip"`` + contract holds -> ``True``.
- ``extraction_mode == "skip"`` + contract BROKEN -> raise
  ``EngineError("E_SKIP_CONTRACT_VIOLATED")`` (claims skip but cannot be
  deterministically skipped -> #5 re-routes to ``llm``).

The validator is pure: it reads no repo/audit state, only the episode.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.episode import Episode
from seahorse.engine import errors
from seahorse.engine.engine import BiTemporalEngine
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)


def _ep(**overrides) -> Episode:
    base: dict = {
        "body": "# Madrid\n",
        "provenance": {"agent": "test", "extraction_mode": "skip"},
        "schema_version": "1.1",
        "created_at": NOW,
        "valid_at": None,
        "invalid_at": None,
        "expired_at": None,
    }
    base.update(overrides)
    # Drop provenance-extraction overrides handled separately.
    return _episode("e1", **base)


def test_skip_path_valid_returns_true():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)  # pure validator, no repo needed
    assert eng.is_valid_skip_path(_ep()) is True


def test_skip_path_valid_with_past_valid_at():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    ep = _ep(valid_at=NOW - timedelta(days=1))  # valid_at <= created_at
    assert eng.is_valid_skip_path(ep) is True


def test_skip_path_not_skip_mode_returns_false():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    ep = _ep(provenance={"agent": "test", "extraction_mode": "llm"})
    assert eng.is_valid_skip_path(ep) is False


def test_skip_path_missing_extraction_mode_returns_false():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    ep = _ep(provenance={"agent": "test"})  # no extraction_mode key
    assert eng.is_valid_skip_path(ep) is False


def test_skip_path_semver_with_patch_is_valid():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    assert eng.is_valid_skip_path(_ep(schema_version="1.2.3")) is True


def test_skip_path_semver_with_prerelease_is_valid():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    assert eng.is_valid_skip_path(_ep(schema_version="3.1.0-beta+build.1")) is True


# --- contract violations -> raise E_SKIP_CONTRACT_VIOLATED -----------------


def test_skip_path_created_at_missing_raises():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    with pytest.raises(errors.EngineError) as exc:
        eng.is_valid_skip_path(_ep(created_at=None))
    assert exc.value.code == errors.E_SKIP_CONTRACT_VIOLATED


def test_skip_path_invalid_at_set_raises():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    with pytest.raises(errors.EngineError) as exc:
        eng.is_valid_skip_path(_ep(invalid_at=NOW))
    assert exc.value.code == errors.E_SKIP_CONTRACT_VIOLATED


def test_skip_path_expired_at_set_raises():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    with pytest.raises(errors.EngineError) as exc:
        eng.is_valid_skip_path(_ep(expired_at=NOW))
    assert exc.value.code == errors.E_SKIP_CONTRACT_VIOLATED


def test_skip_path_valid_at_after_created_at_raises():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    with pytest.raises(errors.EngineError) as exc:
        eng.is_valid_skip_path(_ep(valid_at=NOW + timedelta(days=1)))
    assert exc.value.code == errors.E_SKIP_CONTRACT_VIOLATED


def test_skip_path_bad_schema_version_raises():
    eng = BiTemporalEngine.__new__(BiTemporalEngine)
    with pytest.raises(errors.EngineError) as exc:
        eng.is_valid_skip_path(_ep(schema_version="not-a-semver"))
    assert exc.value.code == errors.E_SKIP_CONTRACT_VIOLATED