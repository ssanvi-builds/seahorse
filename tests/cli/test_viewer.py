"""``seahorse view`` — read-only viewer (ADR-012 l.74).

The viewer is a client of #12 (MemoryFacade): it only calls the read
primitives (``recall`` / ``recall_timeline`` / ``get_vigente``) and NEVER
writes. It degrades honestly on an empty vault (ADR-10). The interaction loop
is driven by an injected input stream for testability.
"""

from __future__ import annotations

import io
from collections.abc import Callable

from seahorse.cli.viewer import run_view
from tests.cli.builders import RecordingFacade, make_episode


def _out() -> io.StringIO:
    return io.StringIO()


def _inputs(*lines: str) -> Callable[[str], str]:
    it = iter(lines)

    def _read(prompt: str) -> str:
        return next(it)

    return _read


class TestViewer:
    def test_empty_vault_honest_degrade(self, recording: RecordingFacade):
        out = _out()
        run_view(recording, out=out, input_stream=_inputs("q"))
        assert "vault vacío / no inicializado" in out.getvalue()
        # No write primitives were called.
        assert recording.remember_calls == []
        assert recording.improve_calls == []
        assert recording.forget_calls == []

    def test_quit_exits(self, recording: RecordingFacade):
        recording.vigente_result = [make_episode("ep-1")]
        out = _out()
        run_view(recording, out=out, input_stream=_inputs("q"))
        assert "bye" in out.getvalue()

    def test_recent_view(self, recording: RecordingFacade):
        recording.vigente_result = [make_episode("ep-1")]
        out = _out()
        run_view(recording, out=out, input_stream=_inputs("1", "q"))
        assert len(recording.recall_calls) == 1
        assert recording.recall_calls[0]["query"] == "recent"

    def test_search_view(self, recording: RecordingFacade):
        recording.vigente_result = [make_episode("ep-1")]
        out = _out()
        run_view(recording, out=out, input_stream=_inputs("2", "how to", "q"))
        assert len(recording.recall_calls) == 1
        assert recording.recall_calls[0]["query"] == "how to"

    def test_timeline_view(self, recording: RecordingFacade):
        recording.vigente_result = [make_episode("ep-1")]
        out = _out()
        run_view(recording, out=out, input_stream=_inputs("3", "ep-1", "q"))
        assert len(recording.recall_timeline_calls) == 1
        assert recording.recall_timeline_calls[0]["anchor"] == "ep-1"

    def test_skills_view_filters_procedural(self, recording: RecordingFacade):
        recording.vigente_result = [
            make_episode("ep-1", cognitive_type="procedural"),
            make_episode("ep-2", cognitive_type="semantic"),
        ]
        out = _out()
        run_view(recording, out=out, input_stream=_inputs("4", "q"))
        assert "ep-1" in out.getvalue()
        assert "ep-2" not in out.getvalue()

    def test_unknown_option(self, recording: RecordingFacade):
        recording.vigente_result = [make_episode("ep-1")]
        out = _out()
        run_view(recording, out=out, input_stream=_inputs("x", "q"))
        assert "unknown option" in out.getvalue()
