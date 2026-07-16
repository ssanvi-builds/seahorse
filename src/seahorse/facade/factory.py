"""Composition root for the real MVP-0 stack (``build_facade``).

#12 ships the facade + primitives but leaves wiring to the caller. #13 (MCP
server startup) and #14 (CLI bootstrap) both need a single, testable seam that
builds a real ``MemoryFacade`` over real ``Storage`` (SQLite) + real
``BiTemporalEngine`` + real ``DisclosureShaperImpl`` + real ``StubWritePath``
with an injectable clock (ADR-10 reproducibility — one shared seam, the same
clock drives the engine and the facade).

This is additive: it does not change #12's surface. It is the function
f5-13/f5-14 reference as ``build_facade`` of #12.

The caller owns the ``Storage`` lifecycle (it must ``close()`` it to release
the SQLite connection pool); ``build_facade`` does not close on the caller's
behalf. Tests use ``tmp_path`` and a context-manager pattern.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from seahorse.disclosure.shaper import DisclosureShaperImpl
from seahorse.engine.engine import BiTemporalEngine
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import FacadeConfig
from seahorse.persistence.storage import Storage
from seahorse.write_path.stub import StubWritePath


def _default_clock() -> datetime:
    return datetime.now(UTC)


def build_facade(
    db_path: Path | str,
    *,
    clock: Callable[[], datetime] | None = None,
    config: FacadeConfig | None = None,
    storage: Storage | None = None,
) -> tuple[MemoryFacade, Storage]:
    """Build a real MVP-0 ``MemoryFacade`` over SQLite + #2 + #8 + #5-stub.

    Returns ``(facade, storage)`` so the caller can ``storage.close()`` when
    done (the server keeps the storage open for the process lifetime; tests
    close it in a fixture teardown). Pass an existing ``storage`` to reuse a
    connection pool — otherwise one is created from ``db_path``.
    """
    own_storage = storage if storage is not None else Storage(db_path)
    engine = BiTemporalEngine(repo=own_storage.episodes, audit=own_storage.audit)
    shaper = DisclosureShaperImpl(
        index_repo=own_storage.episode_index, episode_repo=own_storage.episodes
    )
    write_path = StubWritePath(engine=engine)
    facade = MemoryFacade(
        engine=engine,
        write_path=write_path,
        shaper=shaper,
        clock=clock or _default_clock,
        config=config or FacadeConfig(),
    )
    return facade, own_storage


__all__ = ["build_facade"]