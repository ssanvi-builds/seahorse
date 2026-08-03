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

C8.5 extends the same subprocess pattern to the CORE confinement invariants:
importing ``seahorse.engine`` / ``seahorse.facade`` / ``seahorse.contracts``
must NOT load the confined / out-of-layer deps — ``ruamel`` /
``python-frontmatter`` (confined to ``seahorse/frontmatter/``), ``typer``
(CLI-only), and ``numpy`` / ``sqlite_vec`` (MVP-1). Pydantic is a declared core
dependency (the canonical ``Episode`` model) and is intentionally NOT in the
forbidden set. These guards pin the layer boundaries so a future import that
crosses them fails loud rather than silently coupling the core to a
frontmatter/CLI/MVP-1 dep.

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
# M1-C.3: fastembed + onnxruntime (the 'embeddings' extra) join the heavy set —
# importing seahorse.facade must never pull them (build_fastembed_embedder is
# lazy inside the retrieval regime).
_HEAVY_DEPS = ("numpy", "sqlite_vec", "fastembed", "onnxruntime")

# C8.5: deps confined to a specific layer that the CORE (engine/facade/
# contracts) must never pull at import. ``frontmatter`` is the top-level
# ``python-frontmatter`` package (distinct from ``seahorse.frontmatter``).
_CORE_FORBIDDEN = ("ruamel", "frontmatter", "typer", "numpy", "sqlite_vec", "fastembed", "onnxruntime")


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


def _core_not_loaded_script(import_target: str, label: str) -> str:
    forbidden_repr = "', '".join(_CORE_FORBIDDEN)
    return (
        f"import {import_target}, sys; "
        f"leaked = sorted(d for d in ('{forbidden_repr}',) if d in sys.modules); "
        f"assert not leaked, f'{label} loaded confined/out-of-layer deps: {{leaked}}'; "
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


# ---------------------------------------------------------------------------
# C8.5 — CORE layer-confinement guards (engine / facade / contracts).
# ---------------------------------------------------------------------------


def test_import_engine_does_not_load_confined_deps() -> None:
    _assert_ok(
        _run(_core_not_loaded_script("seahorse.engine", "engine import")),
        "import seahorse.engine",
    )


def test_import_facade_does_not_load_confined_deps() -> None:
    _assert_ok(
        _run(_core_not_loaded_script("seahorse.facade", "facade import")),
        "import seahorse.facade",
    )


def test_import_contracts_does_not_load_confined_deps() -> None:
    _assert_ok(
        _run(_core_not_loaded_script("seahorse.contracts", "contracts import")),
        "import seahorse.contracts",
    )