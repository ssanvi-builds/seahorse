"""Validate EngineError + code constants (Phase 2, owned #2).

The error codes are the stable fail-loud vocabulary consumed across
``apply_fact`` / ``improve`` / ``forget`` / guards. Each must be a unique string
constant so a caller can ``match`` on ``err.code``.
"""

from __future__ import annotations

import pytest

from seahorse.engine import errors


def test_engine_error_carries_code():
    err = errors.EngineError("E_COLLISION_EXISTS", collisions=["x"])
    assert err.code == "E_COLLISION_EXISTS"
    assert "E_COLLISION_EXISTS" in str(err)


def test_engine_error_accepts_extra_context():
    err = errors.EngineError("E_DANGLING_SUPERSEDES", target="ep-1")
    assert err.code == "E_DANGLING_SUPERSEDES"
    assert err.context["target"] == "ep-1"


def test_engine_error_is_exception():
    assert issubclass(errors.EngineError, Exception)
    with pytest.raises(errors.EngineError):
        raise errors.EngineError("E_NOT_IN_MVP_0")


def test_all_codes_are_distinct_strings():
    # Discover every public ``E_*`` constant in the errors module so a new code
    # added later is automatically covered by the uniqueness + prefix check.
    codes = [
        v
        for k, v in vars(errors).items()
        if k.startswith("E_") and isinstance(v, str)
    ]
    assert len(codes) >= 8  # sanity: the known MVP-0 vocabulary
    assert len(codes) == len(set(codes)), f"duplicate codes: {codes}"
    assert all(isinstance(c, str) and c.startswith("E_") for c in codes)