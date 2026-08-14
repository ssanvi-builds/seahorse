"""Install smoke (optional + slow) — validates the "clone, install, run"
promise for a real user: build the wheel, install it in a FRESH venv, and
confirm both console scripts (``seahorse`` + ``seahorse-mcp``) resolve and run.

Gated off by default (it builds + installs, ~30-60s): set
``SEAHORSE_RUN_INSTALL_SMOKE=1`` to run, e.g.
``SEAHORSE_RUN_INSTALL_SMOKE=1 uv run pytest tests/test_install_smoke.py --no-cov``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import venv
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SEAHORSE_RUN_INSTALL_SMOKE") != "1",
    reason="install smoke gated; set SEAHORSE_RUN_INSTALL_SMOKE=1 to run",
)

ROOT = Path(__file__).resolve().parents[1]


def test_install_and_console_scripts_resolve(tmp_path: Path) -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")

    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, f"uv build failed:\n{build.stderr}"
    wheels = list(tmp_path.glob("seahorse-*.whl"))
    assert wheels, f"no wheel produced in {tmp_path}"

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(clear=True, with_pip=True).create(str(venv_dir))
    py = venv_dir / "bin" / "python"

    # Install WITH deps: the console scripts import typer/pydantic at module
    # load (cli/app.py imports typer at top), so --help needs the runtime deps.
    inst = subprocess.run(
        [str(py), "-m", "pip", "install", str(wheels[0])],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert inst.returncode == 0, f"pip install failed:\n{inst.stderr}"

    bin_dir = venv_dir / "bin"

    r = subprocess.run(
        [str(bin_dir / "seahorse"), "--help"], capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f"seahorse --help failed:\n{r.stderr}"
    assert "Seahorse" in r.stdout

    r = subprocess.run(
        [str(bin_dir / "seahorse-mcp"), "--help"], capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f"seahorse-mcp --help failed:\n{r.stderr}"
    assert "io.seahorse.memory/v1" in r.stdout

    # serverInfo.version is single-sourced from the installed metadata: confirm
    # the installed wheel's version is readable and matches the build.
    ver = subprocess.run(
        [str(py), "-c", "import importlib.metadata as m; print(m.version('seahorse'))"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert ver.returncode == 0, ver.stderr
    assert ver.stdout.strip() == wheels[0].name.split("-")[1]
