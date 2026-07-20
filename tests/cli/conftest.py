"""Shared fixtures for #14 CLI tests.

Builders + the ``RecordingFacade`` double live in ``tests/cli/builders.py`` (a
plain module) so test modules can import them directly. This conftest provides
the process-level fixtures: ``invoke`` (the test harness), ``real_facade``
(real SQLite stack), ``vault`` (init'd tmp vault), and ``recording``.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from seahorse.cli.app import main
from seahorse.cli.config import write_default_config
from seahorse.disclosure.shaper import DisclosureShaperImpl
from seahorse.engine.engine import BiTemporalEngine
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import FacadeConfig
from seahorse.persistence.storage import Storage
from seahorse.write_path.stub import StubWritePath
from tests.cli.builders import RecordingFacade  # noqa: F401  (re-exported)

# ---------------------------------------------------------------------------
# invoke harness — the #14 equivalent of #13's stdio loop.
# ---------------------------------------------------------------------------


def invoke(argv: list[str]) -> tuple[int, str, str]:
    """Run ``main(argv)`` with captured streams; return ``(code, out, err)``."""
    out, err = io.StringIO(), io.StringIO()
    so, se = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = main(list(argv))
    finally:
        sys.stdout, sys.stderr = so, se
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Real-stack facade (advancing clock → distinct created_at across writes).
# ---------------------------------------------------------------------------


def _advancing_clock(start: datetime, step: timedelta):
    state = {"t": start}

    def _now() -> datetime:
        t = state["t"]
        state["t"] = t + step
        return t

    return _now


@pytest.fixture()
def real_facade(tmp_path):
    storage = Storage(tmp_path / "cli_e2e.db")
    engine = BiTemporalEngine(repo=storage.episodes, audit=storage.audit)
    shaper = DisclosureShaperImpl(
        index_repo=storage.episode_index, episode_repo=storage.episodes
    )
    write_path = StubWritePath(engine=engine)
    clock = _advancing_clock(datetime(2026, 7, 16, 12, 0, tzinfo=UTC), timedelta(seconds=10))
    f = MemoryFacade(
        engine=engine,
        write_path=write_path,
        shaper=shaper,
        clock=clock,
        config=FacadeConfig(),
    )
    yield f
    storage.close()


# ---------------------------------------------------------------------------
# Vault fixture — init'd tmp vault (config written, no db yet).
# ---------------------------------------------------------------------------


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    write_default_config(v)
    return v


@pytest.fixture()
def recording() -> RecordingFacade:
    return RecordingFacade()


__all__ = ["invoke", "real_facade", "vault", "recording", "RecordingFacade"]