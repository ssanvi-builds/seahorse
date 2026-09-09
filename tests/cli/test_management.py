"""``seahorse.cli.management`` — init / status / uuid7 + reserved stubs."""

from __future__ import annotations

import io
import json

import pytest

from seahorse.cli.config import is_initialized, load_config, write_default_config
from seahorse.cli.errors import CliNotInMVP0
from seahorse.cli.management import RESERVED_COMMANDS, run_init, run_reserved, run_status, run_uuid7


def _out() -> io.StringIO:
    return io.StringIO()


# ---------------------------------------------------------------------------
# init.
# ---------------------------------------------------------------------------


def test_init_creates_vault_and_config(tmp_path):
    v = tmp_path / "newvault"
    assert not v.exists()
    run_init(v, fmt="human", out=_out())
    assert is_initialized(v)
    # config is loadable (round-trips).
    assert load_config(v).top_k == 10


def test_init_creates_missing_parent_dirs(tmp_path):
    v = tmp_path / "a" / "b" / "vault"
    run_init(v, fmt="human", out=_out())
    assert is_initialized(v)


def test_init_idempotent(tmp_path):
    v = tmp_path / "v"
    run_init(v, fmt="human", out=_out())
    run_init(v, fmt="human", out=_out())  # no error
    assert is_initialized(v)


def test_init_json_output(tmp_path):
    v = tmp_path / "v"
    o = _out()
    run_init(v, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["command"] == "init"
    assert obj["status"] == "initialized"


def test_init_human_output(tmp_path):
    v = tmp_path / "v"
    o = _out()
    run_init(v, fmt="human", out=o)
    text = o.getvalue()
    assert "Initialized" in text
    assert str(v) in text


# ---------------------------------------------------------------------------
# status.
# ---------------------------------------------------------------------------


def test_status_human(tmp_path):
    v = tmp_path / "v"
    write_default_config(v)
    cfg = load_config(v)
    o = _out()
    run_status(cfg, fmt="human", out=o)
    text = o.getvalue()
    assert "Seahorse vault" in text
    assert "mode" in text and "skip" in text
    assert "top_k" in text


def test_status_json(tmp_path):
    v = tmp_path / "v"
    write_default_config(v)
    cfg = load_config(v)
    o = _out()
    run_status(cfg, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["initialized"] is True
    assert obj["db_exists"] is False  # no db written yet
    assert obj["top_k"] == 10
    assert obj["memory"] == {"status": "no db yet"}


def _remember_n(facade, n: int) -> None:
    from seahorse.facade.types import RememberPayload

    by = {"agent_id": "test", "session_id": "s1", "source_type": "agent"}
    for i in range(n):
        facade.remember(
            RememberPayload(
                body=f"# fact {i}\n\nDurable fact number {i}.",
                by=by,
                title=f"fact-{i}",
            ),
            extraction_mode="skip",
        )


def test_status_memory_block_counts_episodes(tmp_path):
    v = tmp_path / "v"
    write_default_config(v)
    cfg = load_config(v)
    from seahorse.facade.factory import build_facade

    facade, storage = build_facade(cfg.db_path, retrieval_available=False)
    _remember_n(facade, 3)
    storage.close()
    o = _out()
    run_status(cfg, fmt="json", out=o)
    mem = json.loads(o.getvalue())["memory"]
    assert mem["vigente"] == 3
    assert mem["unconsolidated"] == 3
    assert mem["consolidated_notes"] == 0
    assert mem["oldest_unconsolidated_age_days"] is not None
    assert mem["last_consolidation"] is None
    assert "hint" not in mem  # 3 << UNCONSOLIDATED_WARN


def test_status_memory_hint_at_threshold(tmp_path):
    v = tmp_path / "v"
    write_default_config(v)
    cfg = load_config(v)
    from seahorse.cli.management import UNCONSOLIDATED_WARN
    from seahorse.facade.factory import build_facade

    facade, storage = build_facade(cfg.db_path, retrieval_available=False)
    _remember_n(facade, UNCONSOLIDATED_WARN)
    storage.close()
    o = _out()
    run_status(cfg, fmt="json", out=o)
    mem = json.loads(o.getvalue())["memory"]
    assert mem["unconsolidated"] == UNCONSOLIDATED_WARN
    assert "seahorse consolidate" in mem["hint"]
    human_o = _out()
    run_status(cfg, fmt="human", out=human_o)
    assert "seahorse consolidate" in human_o.getvalue()


def test_status_memory_unavailable_reports_not_crashes(tmp_path, monkeypatch):
    v = tmp_path / "v"
    write_default_config(v)
    cfg = load_config(v)
    (cfg.db_path.parent).mkdir(parents=True, exist_ok=True)
    cfg.db_path.write_bytes(b"not a sqlite db")
    json_o = _out()
    run_status(cfg, fmt="json", out=json_o)  # must not raise
    mem = json.loads(json_o.getvalue())["memory"]
    assert mem["status"].startswith("unavailable:")
    human_o = _out()
    run_status(cfg, fmt="human", out=human_o)  # the degradation reaches the surface
    assert "unavailable:" in human_o.getvalue()


# ---------------------------------------------------------------------------
# uuid7.
# ---------------------------------------------------------------------------


def test_uuid7_human():
    o = _out()
    run_uuid7(fmt="human", out=o)
    val = o.getvalue().strip()
    assert len(val) == 36  # canonical UUID string
    # UUIDv7: version nibble is 7.
    assert val[14] == "7"


def test_uuid7_json():
    o = _out()
    run_uuid7(fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["version"] == 7
    assert obj["uuid"][14] == "7"


def test_uuid7_is_unique():
    a = _out()
    b = _out()
    run_uuid7(fmt="human", out=a)
    run_uuid7(fmt="human", out=b)
    assert a.getvalue() != b.getvalue()


# ---------------------------------------------------------------------------
# reserved stubs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", list(RESERVED_COMMANDS))
def test_reserved_stubs_raise_cli_not_in_mvp0(command):
    with pytest.raises(CliNotInMVP0) as exc_info:
        run_reserved(command)
    assert exc_info.value.exit_code == 75
    # display form uses spaces (index-verify → "index verify").
    assert command.replace("-", " ") in exc_info.value.detail


def test_reserved_unknown_command_still_raises():
    with pytest.raises(CliNotInMVP0):
        run_reserved("nope")


def test_reserved_commands_covers_remaining_stubs():
    """migrate/inspect/index-rebuild are now real commands.

    The reserved surface is the remaining honest-stub set: index-verify
    (needs vec0 from the embedder), vigentes (a later release), activos-ahora
    (a medium-term goal, needs expire). migrate/inspect/index-rebuild are NO
    LONGER reserved.
    """
    expected = {"index-verify", "vigentes", "activos-ahora"}
    assert set(RESERVED_COMMANDS) == expected


def test_reserved_commands_no_longer_contains_unstubbed_ones():
    """migrate/inspect/index-rebuild were promoted to real commands."""
    promoted = {"migrate", "inspect", "index-rebuild"}
    assert promoted.isdisjoint(set(RESERVED_COMMANDS))