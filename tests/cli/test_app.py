"""End-to-end CLI tests via the ``invoke`` harness (#14).

Exercises the real Typer parser + ``main()`` exception-translation seam over
the real facade (SQLite stack). Exit codes are the contract — every path is
asserted against the f5-14 §3.3 layout.
"""

from __future__ import annotations

import json

import pytest

from seahorse.cli.exit_codes import (
    CLI_CONFIG_INVALID,
    CLI_NOT_IN_MVP_0,
    CLI_VAULT_NOT_FOUND,
    EXIT_USAGE,
)
from tests.cli.conftest import invoke

# ---------------------------------------------------------------------------
# Happy paths — real stack.
# ---------------------------------------------------------------------------


def test_init_then_remember_then_recall(vault, tmp_path):
    code, out, err = invoke(["--vault", str(vault), "remember", "hello world"])
    assert code == 0, err
    assert "Remembered" in out

    code, out, err = invoke(["--vault", str(vault), "recall", "anything"])
    assert code == 0, err
    assert "1 results" in out


def test_remember_json_output(vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "remember", "hello"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["status"] == "ACTIVE"
    assert obj["ep_id"]
    # Honest MVP-0 shape (WriteResult, no embedded episode — ADR-10).
    # fact_id may be None with the stub write path; the key is present.
    assert "fact_id" in obj
    assert obj["collisions_detected"] == []


def test_remember_jsonl_shortcut(vault):
    """--jsonl is a shortcut for --format jsonl (f5-14 §3.4)."""
    invoke(["--vault", str(vault), "remember", "a"])
    invoke(["--vault", str(vault), "remember", "b"])
    code, out, err = invoke(["--vault", str(vault), "--jsonl", "recall", "x"])
    assert code == 0, err
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert all(json.loads(line)["ep_id"] for line in lines)


def test_json_and_jsonl_mutually_exclusive(vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "--jsonl", "recall", "x"])
    assert code == EXIT_USAGE, err


def test_quiet_suppresses_stdout(vault):
    """--quiet: stdout empty, command still succeeds, errors still reach stderr."""
    code, out, err = invoke(["--vault", str(vault), "--quiet", "remember", "hello"])
    assert code == 0, err
    assert out == ""  # stdout suppressed
    # An error under --quiet still surfaces on stderr.
    code, out, err = invoke(["--vault", str(vault), "--quiet", "remember", "   "])
    assert code == 64, err
    assert out == ""
    assert "E_EMPTY_BODY" in err


def test_recall_jsonl_one_per_row(vault):
    invoke(["--vault", str(vault), "remember", "a"])
    invoke(["--vault", str(vault), "remember", "b"])
    code, out, err = invoke(["--vault", str(vault), "--format", "jsonl", "recall", "x"])
    assert code == 0, err
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert all(json.loads(line)["ep_id"] for line in lines)


def test_recall_full_round_trip(vault):
    _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "body text"])
    ep = json.loads(out)["ep_id"]
    code, out, err = invoke(["--vault", str(vault), "recall-full", ep])
    assert code == 0, err
    assert "body text" in out  # body hydrated


def test_recall_timeline_round_trip(vault):
    _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "x"])
    ep = json.loads(out)["ep_id"]
    code, out, err = invoke(["--vault", str(vault), "recall-timeline", ep])
    assert code == 0, err
    assert "Timeline:" in out


def test_improve_then_forget_conflict(vault):
    """improve invalidates the original → forget on it raises 87 (Cat B)."""
    _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "x"])
    ep = json.loads(out)["ep_id"]
    code, out, err = invoke(["--vault", str(vault), "improve", ep, "corrected"])
    assert code == 0, err
    code, out, err = invoke(["--vault", str(vault), "forget", ep, "--reason", "r"])
    assert code == 87, err  # InvalidationConflictError
    assert "InvalidationConflictError" in err
    assert "component: #2" in err


# ---------------------------------------------------------------------------
# Management commands.
# ---------------------------------------------------------------------------


def test_init_command_creates_vault(tmp_path):
    v = tmp_path / "fresh"
    code, out, err = invoke(["init", str(v)])
    assert code == 0, err
    assert "Initialized" in out
    assert (v / ".seahorse" / "seahorse.toml").is_file()


def test_status_command(vault):
    code, out, err = invoke(["--vault", str(vault), "status"])
    assert code == 0, err
    assert "Seahorse vault" in out
    assert "skip" in out
    # Onboarding: the retrieval regime is surfaced (hybrid or G2).
    assert "retrieval:" in out


def test_uuid7_command():
    code, out, err = invoke(["uuid7"])
    assert code == 0, err
    val = out.strip()
    assert len(val) == 36 and val[14] == "7"


# ---------------------------------------------------------------------------
# Reserved stubs (Cat C, exit 75).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["expire", "01J"],
        ["revalidate", "01J"],
        ["vigentes"],
        ["activos-ahora"],
        ["index", "verify"],
    ],
)
def test_reserved_commands_exit_75(vault, argv):
    code, out, err = invoke(["--vault", str(vault), *argv])
    assert code == CLI_NOT_IN_MVP_0, err
    assert "CLI_NOT_IN_MVP_0" in err
    assert "reserved in MVP-0" in err


# ---------------------------------------------------------------------------
# CLI-shape usage errors (exit 2).
# ---------------------------------------------------------------------------


def test_bad_source_type_exit_2(vault):
    code, out, err = invoke(["--vault", str(vault), "remember", "x", "--source-type", "alien"])
    assert code == EXIT_USAGE, err
    assert "CLI_USAGE" in err


def test_bad_cognitive_type_exit_2(vault):
    code, out, err = invoke(
        ["--vault", str(vault), "recall", "x", "--cognitive-type", "nope"]
    )
    assert code == EXIT_USAGE, err


def test_bad_now_exit_2(vault):
    _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "x"])
    ep = json.loads(out)["ep_id"]
    code, out, err = invoke(
        ["--vault", str(vault), "forget", ep, "--reason", "r", "--now", "bad"]
    )
    assert code == EXIT_USAGE, err
    assert "ISO-8601" in err


def test_unknown_flag_exit_2(vault):
    code, out, err = invoke(["--vault", str(vault), "remember", "x", "--nope"])
    assert code == EXIT_USAGE, err


def test_no_such_command(vault):
    code, out, err = invoke(["--vault", str(vault), "frobnicate"])
    assert code == EXIT_USAGE, err


# ---------------------------------------------------------------------------
# Cat A — facade/engine domain errors.
# ---------------------------------------------------------------------------


def test_empty_body_exit_64(vault):
    code, out, err = invoke(["--vault", str(vault), "remember", "   "])
    assert code == 64, err
    assert "E_EMPTY_BODY" in err
    assert "component: #12" in err


def test_empty_query_exit_67(vault):
    code, out, err = invoke(["--vault", str(vault), "recall", "   "])
    assert code == 67, err
    assert "E_EMPTY_QUERY" in err
    assert "component: #12" in err


def test_pit_on_recall_index_exit_70(vault):
    code, out, err = invoke(
        ["--vault", str(vault), "recall", "x", "--pit-kind", "state_at",
         "--pit-t", "2026-01-01T00:00:00Z"]
    )
    assert code == 70, err
    assert "E_PIT_RECALL_MVP_0" in err
    assert "component: #12" in err


def test_pit_requires_t_exit_69(vault):
    code, out, err = invoke(
        ["--vault", str(vault), "recall", "x", "--pit-kind", "state_at"]
    )
    assert code == 69, err
    assert "E_PIT_REQUIRES_T" in err
    assert "component: #12" in err


def test_invalid_pit_kind_exit_68(vault):
    code, out, err = invoke(
        ["--vault", str(vault), "recall-timeline", "ep-1",
         "--pit-kind", "bogus", "--pit-t", "2026-01-01T00:00:00Z"]
    )
    assert code == 68, err
    assert "E_INVALID_PIT_KIND" in err
    assert "component: #12" in err


def test_invalid_extraction_mode_exit_66(vault):
    """extraction_mode validation is the facade's → Cat A 66 (not CLI usage 2)."""
    code, out, err = invoke(
        ["--vault", str(vault), "remember", "x", "--extraction-mode", "llm_partial"]
    )
    assert code == 66, err
    assert "E_INVALID_EXTRACTION_MODE" in err
    assert "component: #12" in err


def test_bad_timeline_axis_exit_86(vault):
    """recall-timeline with a non-MVP-0 axis → NotInMVP0 (Cat B 86, component #8)."""
    _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "x"])
    ep = json.loads(out)["ep_id"]
    code, out, err = invoke(["--vault", str(vault), "recall-timeline", ep, "--axis", "graph_bfs"])
    assert code == 86, err
    assert "NotInMVP0" in err
    assert "component: #8" in err


# ---------------------------------------------------------------------------
# Cat B — #8 disclosure contract errors.
# ---------------------------------------------------------------------------


def test_full_batch_too_large_exit_84(vault):
    code, out, err = invoke(
        ["--vault", str(vault), "recall-full", "e1", "e2", "e3", "e4", "e5", "e6"]
    )
    assert code == 84, err
    assert "FullBatchTooLarge" in err
    assert "component: #8" in err


def test_pit_full_not_supported_exit_85(vault):
    _, out, _ = invoke(["--vault", str(vault), "--json", "remember", "x"])
    ep = json.loads(out)["ep_id"]
    code, out, err = invoke(
        ["--vault", str(vault), "recall-full", ep,
         "--pit-kind", "known_at", "--pit-t", "2026-01-01T00:00:00Z"]
    )
    assert code == 85, err
    assert "PitFullNotSupported" in err
    assert "component: #8" in err


def test_not_found_exit_88(vault):
    code, out, err = invoke(
        ["--vault", str(vault), "forget", "01J00000000000000000000000", "--reason", "r"]
    )
    assert code == 88, err
    assert "NotFound" in err
    assert "component: #2" in err


# ---------------------------------------------------------------------------
# Cat C — bootstrap/config errors.
# ---------------------------------------------------------------------------


def test_no_vault_resolved_exit_82(monkeypatch, tmp_path):
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)
    code, out, err = invoke(["recall", "x"])
    assert code == CLI_VAULT_NOT_FOUND, err
    assert "CLI_VAULT_NOT_FOUND" in err


def test_vault_not_existing_dir_exit_82(tmp_path):
    code, out, err = invoke(["--vault", str(tmp_path / "nope"), "status"])
    assert code == CLI_VAULT_NOT_FOUND, err


def test_env_vault_resolves(monkeypatch, vault):
    monkeypatch.setenv("SEAHORSE_VAULT", str(vault))
    code, out, err = invoke(["status"])
    assert code == 0, err
    assert "Seahorse vault" in out


def test_cwd_vault_resolves(monkeypatch, vault):
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    monkeypatch.chdir(vault)
    code, out, err = invoke(["status"])
    assert code == 0, err


def test_config_invalid_exit_83(tmp_path):
    """A corrupt seahorse.toml → exit 83 (load_config raises CliConfigInvalid)."""
    v = tmp_path / "v"
    (v / ".seahorse").mkdir(parents=True)
    (v / ".seahorse" / "seahorse.toml").write_text("not = valid = toml =")
    code, out, err = invoke(["--vault", str(v), "status"])
    assert code == CLI_CONFIG_INVALID, err
    assert "CLI_CONFIG_INVALID" in err


# ---------------------------------------------------------------------------
# --json error payload shape.
# ---------------------------------------------------------------------------


def test_error_payload_is_json_when_json_flag(vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "remember", "   "])
    assert code == 64
    obj = json.loads(err)
    # f5-14 §3.3: errors carry an {"error": {...}} envelope.
    assert "error" in obj
    payload = obj["error"]
    assert payload["seahorse_code"] == "E_EMPTY_BODY"
    assert payload["exit_code"] == 64
    assert payload["component"] == "#12"


# ---------------------------------------------------------------------------
# Onboarding: model-download notice + retrieval regime in status.
# ---------------------------------------------------------------------------


def test_first_command_skips_global_value_flags():
    from seahorse.cli.app import _first_command

    assert _first_command(["--vault", "/tmp/x", "remember", "hi"]) == "remember"
    assert _first_command(["--json", "recall", "q"]) == "recall"
    assert _first_command(["--format", "json", "status"]) == "status"
    assert _first_command(["-q", "init", "/tmp/x"]) == "init"
    assert _first_command(["--json"]) is None


def test_announce_model_download_when_not_cached(monkeypatch, capsys):
    from seahorse.cli.app import CliContext, _announce_model_download

    monkeypatch.setattr("seahorse.cli.app._CURRENT_ARGV", ["remember", "hi"])
    monkeypatch.setattr(
        "seahorse.embeddings.fastembed_backend.model_cached", lambda: False
    )
    _announce_model_download(CliContext(fmt="human", quiet=False))
    assert "First run" in capsys.readouterr().out


def test_announce_model_download_silent_when_cached(monkeypatch, capsys):
    from seahorse.cli.app import CliContext, _announce_model_download

    monkeypatch.setattr("seahorse.cli.app._CURRENT_ARGV", ["remember", "hi"])
    monkeypatch.setattr(
        "seahorse.embeddings.fastembed_backend.model_cached", lambda: True
    )
    _announce_model_download(CliContext(fmt="human", quiet=False))
    assert capsys.readouterr().out == ""


def test_announce_model_download_silent_for_non_embed_command(monkeypatch, capsys):
    from seahorse.cli.app import CliContext, _announce_model_download

    monkeypatch.setattr("seahorse.cli.app._CURRENT_ARGV", ["status"])
    monkeypatch.setattr(
        "seahorse.embeddings.fastembed_backend.model_cached", lambda: False
    )
    _announce_model_download(CliContext(fmt="human", quiet=False))
    assert capsys.readouterr().out == ""


def test_announce_model_download_silent_in_json_mode(monkeypatch, capsys):
    from seahorse.cli.app import CliContext, _announce_model_download

    monkeypatch.setattr("seahorse.cli.app._CURRENT_ARGV", ["remember", "hi"])
    monkeypatch.setattr(
        "seahorse.embeddings.fastembed_backend.model_cached", lambda: False
    )
    _announce_model_download(CliContext(fmt="json", quiet=False))
    assert capsys.readouterr().out == ""