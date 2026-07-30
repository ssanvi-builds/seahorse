"""C8.2 import-laziness guard — importing the facade/MCP must NOT cascade into
the MVP-1 concrete repos (``vector_index`` / ``fts_index``), and therefore
must not load ``numpy`` / ``sqlite_vec``.

When #6 lands the real sqlite-vec + FTS5 impls, those modules will import
``sqlite_vec`` (and possibly ``numpy``) at module top. If ``storage.py`` kept
importing them top-level, every ``import seahorse.facade`` (and
``import seahorse.mcp``) would eagerly load the heavy MVP-1 deps — breaking the
"clone-and-run, zero runtime deps in MVP-0" promise at *import* time, before
any tool is even called. C8.2 makes the ``storage.py`` import of the two MVP-1
repos lazy (inside the ``vector`` / ``fts`` property accessors), so the cascade
is broken: importing the facade/MCP never touches ``vector_index`` /
``fts_index`` until someone actually accesses ``Storage.vector`` /
``Storage.fts``.

These guards run in a fresh subprocess so the assertion sees a clean
``sys.modules`` (the test process itself has the persistence layer loaded by
other tests). Mirrors the ruamel-confinement guard in
``tests/cli/test_vault_ops.py::test_cli_app_import_does_not_load_ruamel``.
"""

from __future__ import annotations

import subprocess
import sys

# Modules that MUST NOT be imported as a side effect of `import seahorse.facade`
# or `import seahorse.mcp`. The two MVP-1 concrete repos are the laziness
# boundary; numpy and sqlite_vec are the heavy deps they will pull in MVP-1.
_LAZY_MODULES = (
    "seahorse.persistence.vector_index",
    "seahorse.persistence.fts_index",
)
_HEAVY_DEPS = ("numpy", "sqlite_vec")


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )


def _assert_ok(result: subprocess.CompletedProcess[str], label: str) -> None:
    assert result.returncode == 0, (
        f"{label} guard failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.stdout.strip() == "ok", (
        f"{label} guard unexpected output:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def _not_loaded_script(import_target: str, label: str) -> str:
    lazy_repr = "', '".join(_LAZY_MODULES)
    heavy_repr = "', '".join(_HEAVY_DEPS)
    return (
        f"import {import_target}, sys; "
        f"leaked_lazy = sorted(m for m in ('{lazy_repr}',) if m in sys.modules); "
        f"leaked_heavy = sorted(d for d in ('{heavy_repr}',) if d in sys.modules); "
        f"assert not leaked_lazy, f'{label} loaded lazy MVP-1 repos: {{leaked_lazy}}'; "
        f"assert not leaked_heavy, f'{label} loaded heavy deps: {{leaked_heavy}}'; "
        "print('ok')"
    )


def test_import_facade_does_not_load_mvp1_repos() -> None:
    _assert_ok(
        _run(_not_loaded_script("seahorse.facade", "facade import")),
        "import seahorse.facade",
    )


def test_import_mcp_does_not_load_mvp1_repos() -> None:
    # mcp re-exports build_facade (imports the facade package), so it must
    # inherit the laziness — no MVP-1 repo loaded at import time.
    _assert_ok(
        _run(_not_loaded_script("seahorse.mcp", "mcp import")),
        "import seahorse.mcp",
    )


def test_storage_vector_access_loads_mvp1_repo() -> None:
    # The lazy import must STILL resolve when the repo is actually needed:
    # accessing Storage.vector triggers the import + construction. This pins
    # that the seam is lazy, not absent (the repo is reachable on demand).
    script = (
        "import tempfile, pathlib, sys; "
        "from seahorse.persistence.storage import Storage; "
        "db = pathlib.Path(tempfile.mkdtemp()) / 'x.db'; "
        "s = Storage(db); "
        "assert 'seahorse.persistence.vector_index' not in sys.modules, "
        "'vector_index should not load until .vector is accessed'; "
        "_ = s.vector; "
        "assert 'seahorse.persistence.vector_index' in sys.modules, "
        "'vector_index not loaded on .vector access'; "
        "_ = s.fts; "
        "assert 'seahorse.persistence.fts_index' in sys.modules, "
        "'fts_index not loaded on .fts access'; "
        "s.close(); "
        "print('ok')"
    )
    _assert_ok(_run(script), "Storage.vector / .fts access")