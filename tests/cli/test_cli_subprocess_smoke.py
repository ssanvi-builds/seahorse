"""Subprocess CLI smoke — the systematic functional review, committed as
regression tests.

In-process tests (``invoke()``) cannot catch what a real user hits: the
console-script wrapper, ``sys.argv`` parsing, the real process boundary, env
discovery, and the actual ``seahorse`` on PATH. These tests spawn the installed
``seahorse`` binary per step and assert exit code + parse JSON, covering the
full first-release matrix: init → status → remember → recall → recall-full →
recall-timeline → improve → forget → inspect → migrate → uuid7, the honest
exit codes (64/67/70/88), the reserved stubs (75), and the --json/--jsonl
formats.

Note on the parsing boundary: ``--vault``/``--json``/``--jsonl`` are GLOBAL Typer options
(parsed by the callback) so they MUST precede the subcommand. Success payloads
go to stdout; the ``{"error": {...}}`` envelope goes to stderr (``main()``'s
``_emit_error`` writes to ``sys.stderr``), so error cases parse stderr.

Pattern mirrors the subprocess guardian in ``tests/cli/test_vault_ops.py``
(lines 287-298): subprocess against the real installed binary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _seahorse_bin() -> str | None:
    """The installed ``seahorse`` console script — MUST live in the same venv
    that owns the running pytest (``sys.executable``'s bin dir).

    No PATH fallback: a ``shutil.which`` hit could resolve a stale ``seahorse``
    from a different venv with the same first-release surface, passing the suite without
    exercising the code under test. Requiring the co-located binary guarantees
    the subprocess runs the same install as the test code.
    """
    venv_bin = Path(sys.executable).parent / "seahorse"
    return str(venv_bin) if venv_bin.exists() else None


SEAHORSE = _seahorse_bin()
pytestmark = pytest.mark.skipif(SEAHORSE is None, reason="seahorse console script not found")


def _run(
    cmd_args: list[str],
    *,
    vault: Path | None = None,
    json_out: bool = False,
    jsonl_out: bool = False,
    env: dict | None = None,
):
    # Global options precede the subcommand (Typer callback parsing).
    cmd = [SEAHORSE]  # type: ignore[list-item]
    if vault is not None:
        cmd += ["--vault", str(vault)]
    if json_out:
        cmd += ["--json"]
    if jsonl_out:
        cmd += ["--jsonl"]
    cmd += list(cmd_args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    r = _run(["init", str(v)])
    assert r.returncode == 0, f"init failed:\n{r.stderr}"
    return v


def _j(stdout: str):
    return json.loads(stdout)


# ---------------------------------------------------------------------------
# init + status
# ---------------------------------------------------------------------------


def test_init_creates_config(tmp_path: Path) -> None:
    v = tmp_path / "vault"
    r = _run(["init", str(v)])
    assert r.returncode == 0, r.stderr
    assert (v / ".seahorse" / "seahorse.toml").is_file()


def test_status_db_absent_before_first_write(vault: Path) -> None:
    r = _run(["status"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    payload = _j(r.stdout)
    assert payload["initialized"] is True
    assert payload["db_exists"] is False


# ---------------------------------------------------------------------------
# full lifecycle
# ---------------------------------------------------------------------------


def test_full_lifecycle_remember_recall_improve_forget(vault: Path) -> None:
    # A survivor episode with NO "madrid" token — the positive control for
    # both recall assertions. First-release recall ignores the query beyond a
    # non-empty check and returns the full current-state listing, so
    # query="madrid" must surface the survivor too; and after improve+forget
    # the survivor is the only current-state episode left, so its presence (not
    # just the absence of the invalidated ids) is what proves forget worked.
    r = _run(
        ["remember", "Paris is the capital of France", "--title", "geo"],
        vault=vault,
        json_out=True,
    )
    assert r.returncode == 0, r.stderr
    survivor_id = _j(r.stdout)["ep_id"]

    r = _run(["remember", "Sergio lives in Madrid", "--title", "home"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    wr = _j(r.stdout)
    assert wr["status"] == "ACTIVE"
    ep_id = wr["ep_id"]

    # recall("madrid") returns the FULL current-state listing — both episodes
    # appear regardless of the query token (proves the first release does NOT
    # filter by query).
    r = _run(["recall", "madrid"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    rows = _j(r.stdout)
    ids = [row["ep_id"] for row in rows]
    assert ep_id in ids
    assert survivor_id in ids

    # --top-k 1 clamps the listing to one row (proves k is honored + the
    # listing is ordered, not capped to zero somewhere upstream).
    r = _run(["recall", "madrid", "-k", "1"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    clamped = _j(r.stdout)
    assert len(clamped) == 1

    r = _run(["recall-full", ep_id], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    details = _j(r.stdout)
    assert len(details) == 1
    assert details[0]["episode"]["id"] == ep_id
    assert details[0]["episode"]["body"] == "Sergio lives in Madrid"

    r = _run(["recall-timeline", ep_id], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    window = _j(r.stdout)
    assert window["anchor_ep_id"] == ep_id

    r = _run(
        ["improve", ep_id, "Sergio lives in Barcelona", "--reason", "correction"],
        vault=vault,
        json_out=True,
    )
    assert r.returncode == 0, r.stderr
    new_ep = _j(r.stdout)
    assert new_ep["id"] != ep_id
    new_id = new_ep["id"]

    r = _run(["forget", new_id, "--reason", "done"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    forgotten = _j(r.stdout)
    assert forgotten["invalid_at"] is not None

    # After improve (invalidates ep_id) + forget (invalidates new_id), the ONLY
    # current-state episode is the survivor — assert its presence AND the exact
    # count, not just the absence of the invalidated ids (which would pass
    # vacuously against an empty listing).
    r = _run(["recall", "madrid"], vault=vault, json_out=True)
    assert r.returncode == 0
    ids = [row["ep_id"] for row in _j(r.stdout)]
    assert ep_id not in ids
    assert new_id not in ids
    assert survivor_id in ids
    assert len(ids) == 1


# ---------------------------------------------------------------------------
# error exit codes — envelope on stderr
# ---------------------------------------------------------------------------


def test_remember_empty_body_exits_64(vault: Path) -> None:
    r = _run(["remember", ""], vault=vault, json_out=True)
    assert r.returncode == 64
    assert _j(r.stderr)["error"]["seahorse_code"] == "E_EMPTY_BODY"


def test_recall_empty_query_exits_67(vault: Path) -> None:
    r = _run(["recall", ""], vault=vault, json_out=True)
    assert r.returncode == 67
    assert _j(r.stderr)["error"]["seahorse_code"] == "E_EMPTY_QUERY"


def test_recall_pit_exits_70(vault: Path) -> None:
    r = _run(
        ["recall", "q", "--pit-kind", "state_at", "--pit-t", "2026-07-01T00:00:00"],
        vault=vault,
        json_out=True,
    )
    assert r.returncode == 70
    assert _j(r.stderr)["error"]["seahorse_code"] == "E_PIT_RECALL_MVP_0"


def test_improve_not_found_exits_88(vault: Path) -> None:
    r = _run(["improve", "nope-such-ep", "x"], vault=vault, json_out=True)
    assert r.returncode == 88
    assert _j(r.stderr)["error"]["exception_class"] == "NotFound"


# ---------------------------------------------------------------------------
# honest stubs (CLI_NOT_IN_MVP_0 = 75) — envelope on stderr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["expire", "0194d5a0-0000-7000-8000-000000000000"],
        ["revalidate", "0194d5a0-0000-7000-8000-000000000000"],
        ["vigentes"],
        ["activos-ahora"],
        ["index", "verify"],
    ],
)
def test_reserved_stubs_exit_75(vault: Path, args: list[str]) -> None:
    r = _run(args, vault=vault, json_out=True)
    assert r.returncode == 75
    assert _j(r.stderr)["error"]["cli_code"] == "CLI_NOT_IN_MVP_0"


# ---------------------------------------------------------------------------
# formats
# ---------------------------------------------------------------------------


def test_json_and_jsonl_formats(vault: Path) -> None:
    r = _run(["remember", "alpha fact", "--title", "t"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    assert _j(r.stdout)["status"] == "ACTIVE"

    r = _run(["recall", "alpha"], vault=vault, jsonl_out=True)
    assert r.returncode == 0, r.stderr
    lines = [line for line in r.stdout.splitlines() if line.strip()]
    assert len(lines) >= 1
    assert "ep_id" in _j(lines[0])


# ---------------------------------------------------------------------------
# management: inspect / migrate / uuid7 / index rebuild
# ---------------------------------------------------------------------------


def test_inspect_after_write_reports_schema(vault: Path) -> None:
    r = _run(["remember", "body one", "--title", "t"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    r = _run(["inspect"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    snap = _j(r.stdout)
    assert snap["db_exists"] is True
    assert snap["schema_version"] == 12
    assert snap["episodes"] >= 1


def test_migrate_idempotent(vault: Path) -> None:
    r = _run(["remember", "body", "--title", "t"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    r = _run(["migrate", "--up-to", "10"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    assert _j(r.stdout)["schema_version"] == 12


def test_uuid7_emits_valid_v7(vault: Path) -> None:
    r = _run(["uuid7"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    payload = _j(r.stdout)
    assert payload["version"] == 7
    # UUIDv7: the version nibble (char index 14) is '7'.
    assert payload["uuid"][14] == "7"


def test_index_rebuild_on_empty_vault(vault: Path) -> None:
    # No .md notes → indexes 0, exit 0 (exercises the rebuild subprocess path).
    r = _run(["index", "rebuild"], vault=vault, json_out=True)
    assert r.returncode == 0, r.stderr
    payload = _j(r.stdout)
    assert payload["command"] == "index rebuild"
    assert payload["indexed"] == 0
    assert payload["skipped"] == 0


# ---------------------------------------------------------------------------
# env discovery (SEAHORSE_VAULT)
# ---------------------------------------------------------------------------


def test_seahorse_vault_env_resolves(tmp_path: Path) -> None:
    v = tmp_path / "vault"
    _run(["init", str(v)])
    r = _run(["status"], json_out=True, env={**os.environ, "SEAHORSE_VAULT": str(v)})
    assert r.returncode == 0, r.stderr
    assert _j(r.stdout)["initialized"] is True
