"""Validate WriteGuards (I1-I11) (Phase 3, owned #2).

The guard chain runs at write-time before ``repo.append`` / ``repo.set_invalid_at``
and raises a typed error on the first violated invariant. SO-4a amends I2: the
allowed set for an arbitrary ``valid_at`` is ``{human, importer, system}``;
``agent`` / ``project_doc`` are restricted to ``null`` or ``now``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import InvalidationConflictError
from seahorse.engine import errors
from seahorse.engine.guards import WriteGuards
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(days=10)
FUTURE = NOW + timedelta(days=10)


class _FakeRepo:
    """Minimal repo for supersedes_exists: ``get`` backed by a dict."""

    def __init__(self, episodes: dict[str, object] | None = None) -> None:
        self._episodes = episodes or {}

    def get(self, ep_id: str):  # noqa: ANN001
        return self._episodes.get(ep_id)

    @contextmanager
    def atomic(self) -> Iterator[None]:
        yield


# --- I1 created_at engine-owned ------------------------------------------------


def test_i1_passes_when_created_at_set():
    ep = _episode(created_at=NOW)
    WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)


def test_i1_raises_when_created_at_none():
    ep = _episode(created_at=None)
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)
    assert exc.value.code == errors.E_CREATED_AT_ENGINE_OWNED


# --- I2 valid_at by source (SO-4a) ---------------------------------------------


@pytest.mark.parametrize("source_type", ["human", "importer", "system"])
def test_i2_allows_arbitrary_valid_at_for_allowed_set(source_type):
    # Past, future, and null all OK for the SO-4a allowed set.
    for va in (PAST, FUTURE, None):
        ep = _episode(source_type=source_type, valid_at=va, created_at=NOW)
        WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)


@pytest.mark.parametrize("source_type", ["agent", "project_doc"])
def test_i2_restricted_sources_allow_only_null_or_now(source_type):
    # null and now are the only permitted values for agent/project_doc.
    for va in (None, NOW):
        ep = _episode(source_type=source_type, valid_at=va, created_at=NOW)
        WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)


@pytest.mark.parametrize("source_type", ["agent", "project_doc"])
@pytest.mark.parametrize("va", [PAST, FUTURE])
def test_i2_restricted_sources_reject_custom_valid_at(source_type, va):
    ep = _episode(source_type=source_type, valid_at=va, created_at=NOW)
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)
    assert exc.value.code == errors.E_VALID_AT_HUMAN_ONLY


# --- I5 monotonic (null-safe) -------------------------------------------------


def test_i5_monotonic_ok_when_both_none():
    ep = _episode(created_at=NOW)
    WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)


def test_i5_monotonic_ok_when_valid_le_invalid():
    # human source so I2 permits the arbitrary valid_at; I5 then checks the pair.
    ep = _episode(source_type="human", created_at=NOW, valid_at=PAST, invalid_at=NOW)
    WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)


def test_i5_monotonic_violation_valid_gt_invalid():
    ep = _episode(source_type="human", created_at=NOW, valid_at=FUTURE, invalid_at=PAST)
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)
    assert exc.value.code == errors.E_MONOTONICITY_VIOLATED


def test_i5_monotonic_violation_created_gt_expired():
    ep = _episode(created_at=FUTURE, expired_at=PAST)
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)
    assert exc.value.code == errors.E_MONOTONICITY_VIOLATED


# --- I4 expired_at null MVP-0 -------------------------------------------------


def test_i4_rejects_expired_at_non_null():
    ep = _episode(created_at=NOW, expired_at=FUTURE)
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)
    assert exc.value.code == errors.E_EXPIRED_AT_NON_NULL


# --- supersedes exists --------------------------------------------------------


def test_supersedes_none_is_ok():
    ep = _episode(created_at=NOW, supersedes=None)
    WriteGuards().validate(ep, repo=_FakeRepo(), op="apply_fact", now=NOW)


def test_supersedes_existing_is_ok():
    target = _episode("old")
    ep = _episode(created_at=NOW, supersedes="old")
    WriteGuards().validate(ep, repo=_FakeRepo({"old": target}), op="apply_fact", now=NOW)


def test_supersedes_dangling_raises():
    ep = _episode(created_at=NOW, supersedes="ghost")
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo({}), op="apply_fact", now=NOW)
    assert exc.value.code == errors.E_DANGLING_SUPERSEDES


# --- forget chain (I3 / I5 valid_le_now / I6 / I7) ----------------------------


def test_forget_chain_ok_on_vigente_active():
    ep = _episode(invalid_at=None, expired_at=None, valid_at=PAST)
    WriteGuards().validate(ep, repo=_FakeRepo(), op="forget", now=NOW)


def test_forget_i3_rejects_already_invalidated():
    ep = _episode(invalid_at=PAST)
    with pytest.raises(InvalidationConflictError):
        WriteGuards().validate(ep, repo=_FakeRepo(), op="forget", now=NOW)


def test_forget_i5_rejects_pending_ingest():
    ep = _episode(valid_at=FUTURE, invalid_at=None)
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo(), op="forget", now=NOW)
    assert exc.value.code == errors.E_PENDING_CANNOT_INVALIDATE


def test_forget_i6_rejects_decayed_episode():
    ep = _episode(invalid_at=None, expired_at=PAST)
    with pytest.raises(InvalidationConflictError):
        WriteGuards().validate(ep, repo=_FakeRepo(), op="forget", now=NOW)


def test_forget_i7_expired_at_untouched_no_raise():
    # _i7 is a documented structural guard; calling it must not raise.
    ep = _episode(invalid_at=None, expired_at=None)
    WriteGuards().validate(ep, repo=_FakeRepo(), op="forget", now=NOW)


# --- unknown / mediano ops ----------------------------------------------------


def test_validate_unknown_op_raises():
    ep = _episode(created_at=NOW)
    with pytest.raises(ValueError):
        WriteGuards().validate(ep, repo=_FakeRepo(), op="bogus", now=NOW)


def test_validate_revalidate_is_mvp1_stub():
    ep = _episode(created_at=NOW)
    with pytest.raises(errors.EngineError) as exc:
        WriteGuards().validate(ep, repo=_FakeRepo(), op="revalidate", now=NOW)
    assert exc.value.code == errors.E_NOT_IN_MVP_0


def test_validate_improve_runs_atomicity_marker():
    # op="improve" routes through the apply_fact guards on the NEW episode
    # inside repo.atomic(); the validate(op="improve") branch is the atomicity
    # marker documented by I8. It must not raise for a well-formed episode.
    ep = _episode(created_at=NOW, supersedes="old")
    WriteGuards().validate(ep, repo=_FakeRepo({"old": _episode("old")}), op="improve", now=NOW)