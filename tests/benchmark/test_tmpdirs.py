"""Tests for the benchmark package's scoped temp workspaces.

The L11 hardening loop found 209 stale ``seahorse-bench-*``-style directories
(~313MB) leaked by bare ``tempfile.mkdtemp`` calls; every benchmark mkdtemp
now goes through ``mkdtemp_scoped``, which cleans up at interpreter exit.
"""

from __future__ import annotations

from seahorse.benchmark._tmpdirs import _SCOPED, _cleanup_scoped, mkdtemp_scoped


class TestMkdtempScoped:
    def test_returns_existing_dir(self) -> None:
        path = mkdtemp_scoped("seahorse-test-scoped-")
        try:
            assert path.is_dir()
            assert path.name.startswith("seahorse-test-scoped-")
        finally:
            _cleanup_scoped()

    def test_cleanup_removes_registered_dirs(self) -> None:
        path = mkdtemp_scoped("seahorse-test-scoped-")
        (path / "bench.db").write_bytes(b"x")
        assert path in _SCOPED
        _cleanup_scoped()
        assert not path.exists()
        assert path not in _SCOPED

    def test_cleanup_is_idempotent(self) -> None:
        mkdtemp_scoped("seahorse-test-scoped-")
        _cleanup_scoped()
        _cleanup_scoped()  # second pass over an empty registry must not raise