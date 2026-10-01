"""Tests for ``cli.procutil`` — pid-file round-trips and kernel liveness.

``procutil`` is the shared pid-file machinery extracted from the observer
(``seahorse.cli.observe``) so every CLI-managed daemon — the observer today,
``seahorse remote`` next — has ONE implementation of the file format (an
integer pid, one line, utf-8) and the liveness semantics (signal 0; a
permission error means "alive but not ours").
"""

from __future__ import annotations

import os
import subprocess

import pytest

from seahorse.cli import procutil

# --- read_pid --------------------------------------------------------------


def test_read_pid_absent_returns_none(tmp_path):
    assert procutil.read_pid(tmp_path / "missing.pid") is None


def test_read_pid_roundtrip(tmp_path):
    pid_file = tmp_path / "proc.pid"
    pid_file.write_text("12345", encoding="utf-8")
    assert procutil.read_pid(pid_file) == 12345


def test_read_pid_strips_surrounding_whitespace(tmp_path):
    pid_file = tmp_path / "proc.pid"
    pid_file.write_text(" 12345 \n", encoding="utf-8")
    assert procutil.read_pid(pid_file) == 12345


def test_read_pid_corrupt_content_returns_none(tmp_path):
    pid_file = tmp_path / "proc.pid"
    pid_file.write_text("not-a-pid", encoding="utf-8")
    assert procutil.read_pid(pid_file) is None


def test_read_pid_directory_returns_none(tmp_path):
    # A directory at the pid-file path is not a pid file (is_file guard).
    assert procutil.read_pid(tmp_path) is None


# --- write_pid -------------------------------------------------------------


def test_write_pid_creates_parent_directories(tmp_path):
    pid_file = tmp_path / "nested" / "dir" / "proc.pid"
    procutil.write_pid(pid_file, 4242)
    assert pid_file.read_text(encoding="utf-8") == "4242"


def test_write_pid_overwrites_existing(tmp_path):
    pid_file = tmp_path / "proc.pid"
    procutil.write_pid(pid_file, 1)
    procutil.write_pid(pid_file, 2)
    assert procutil.read_pid(pid_file) == 2


# --- remove_pid ------------------------------------------------------------


def test_remove_pid_unlinks_present_file(tmp_path):
    pid_file = tmp_path / "proc.pid"
    pid_file.write_text("1", encoding="utf-8")
    procutil.remove_pid(pid_file)
    assert not pid_file.exists()


def test_remove_pid_absent_file_is_noop(tmp_path):
    procutil.remove_pid(tmp_path / "missing.pid")


# --- pid_alive --------------------------------------------------------------


def test_pid_alive_true_for_current_process():
    assert procutil.pid_alive(os.getpid()) is True


def test_pid_alive_false_for_reaped_process():
    # A waited-for child is reaped by Popen.wait(), so its pid is truly
    # gone (a zombie would still answer signal 0).
    proc = subprocess.Popen(["sleep", "0"])
    proc.wait()
    assert procutil.pid_alive(proc.pid) is False


def test_pid_alive_false_on_process_lookup_error(monkeypatch: pytest.MonkeyPatch):
    def gone(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr("seahorse.cli.procutil.os.kill", gone)
    assert procutil.pid_alive(12345) is False


def test_pid_alive_true_on_permission_error(monkeypatch: pytest.MonkeyPatch):
    # Alive but owned by someone else — treat as running, never kill.
    def denied(pid, sig):
        raise PermissionError()

    monkeypatch.setattr("seahorse.cli.procutil.os.kill", denied)
    assert procutil.pid_alive(12345) is True