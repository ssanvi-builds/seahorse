"""``--verbose`` — per-operation timing on stderr.

The flag is a global callback option; when set, the run_* functions wrap their
facade call in ``_timed`` and write ``[verbose] <label> took <X>ms`` to stderr
(never stdout, the structured output channel). ``verbose=False`` is a
zero-cost passthrough.
"""

from __future__ import annotations

import io
import re

from seahorse.cli.primitives import _timed
from tests.cli.conftest import invoke


def _out() -> io.StringIO:
    return io.StringIO()


class TestTimedHelper:
    def test_verbose_off_is_passthrough(self, capsys) -> None:
        result = _timed("op", lambda: 42, verbose=False)
        assert result == 42
        assert capsys.readouterr().err == ""

    def test_verbose_on_writes_timing_to_stderr(self, capsys) -> None:
        result = _timed("op", lambda: 42, verbose=True)
        assert result == 42
        err = capsys.readouterr().err
        assert re.match(r"\[verbose\] op took \d+\.\d+ms\n", err)


class TestVerboseFlagE2E:
    def test_verbose_emits_timing_on_stderr(self, vault) -> None:
        code, out, err = invoke(["--vault", str(vault), "--verbose", "remember", "x"])
        assert code == 0, err
        assert "[verbose] remember took" in err

    def test_without_verbose_no_timing(self, vault) -> None:
        code, out, err = invoke(["--vault", str(vault), "remember", "x"])
        assert code == 0, err
        assert "[verbose]" not in err

    def test_short_flag_v(self, vault) -> None:
        code, out, err = invoke(["--vault", str(vault), "-v", "remember", "x"])
        assert code == 0, err
        assert "[verbose] remember took" in err
