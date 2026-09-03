"""Tests for ``seahorse.cli.agent_instructions`` — delimited CLAUDE.md block.

Contract: idempotent merge, user content preserved byte-for-byte, stale
blocks updated in place, clean removal, SEAHORSE_CLAUDE_MD override.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seahorse.cli.agent_instructions import (
    BEGIN_MARKER,
    END_MARKER,
    claude_md_path,
    install_agent_instructions,
    installed,
    instructions_block,
    remove_agent_instructions,
)


@pytest.fixture()
def md_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "CLAUDE.md"
    monkeypatch.setenv("SEAHORSE_CLAUDE_MD", str(path))
    return path


class TestInstall:
    def test_fresh_file_gets_only_the_block(self, md_path: Path) -> None:
        ok, detail = install_agent_instructions()
        assert ok
        text = md_path.read_text()
        assert text == instructions_block() + "\n"
        assert "written" in detail

    def test_idempotent_second_call_is_noop(self, md_path: Path) -> None:
        install_agent_instructions()
        before = md_path.read_text()
        ok, detail = install_agent_instructions()
        assert ok and "already" in detail
        assert md_path.read_text() == before

    def test_user_content_preserved_around_block(self, md_path: Path) -> None:
        md_path.write_text("# My rules\n\nBe terse.\n")
        install_agent_instructions()
        text = md_path.read_text()
        assert text.startswith("# My rules\n\nBe terse.\n\n")
        assert text.endswith(instructions_block() + "\n")
        assert installed()

    def test_stale_block_updated_in_place(self, md_path: Path) -> None:
        stale = f"{BEGIN_MARKER}\n# old content\n{END_MARKER}"
        md_path.write_text(f"keep me\n\n{stale}\n\ntrailing note\n")
        ok, detail = install_agent_instructions()
        assert ok and "updated" in detail
        text = md_path.read_text()
        assert "keep me" in text and "trailing note" in text
        assert "old content" not in text
        assert text.count(BEGIN_MARKER) == 1

    def test_creates_parent_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "deep" / "dir" / "CLAUDE.md"
        monkeypatch.setenv("SEAHORSE_CLAUDE_MD", str(path))
        ok, _ = install_agent_instructions()
        assert ok and path.exists()


class TestRemove:
    def test_remove_leaves_user_content(self, md_path: Path) -> None:
        md_path.write_text("# My rules\n\nBe terse.\n")
        install_agent_instructions()
        ok, _ = remove_agent_instructions()
        assert ok
        text = md_path.read_text()
        assert text == "# My rules\n\nBe terse.\n"
        assert not installed()

    def test_remove_file_with_only_block_empties_it(self, md_path: Path) -> None:
        install_agent_instructions()
        ok, _ = remove_agent_instructions()
        assert ok
        assert md_path.read_text().strip() == ""

    def test_remove_when_absent_is_ok(self, md_path: Path) -> None:
        ok, _ = remove_agent_instructions()
        assert ok

    def test_no_double_blank_gap_after_remove(self, md_path: Path) -> None:
        md_path.write_text("top\n")
        install_agent_instructions()
        remove_agent_instructions()
        assert "\n\n\n" not in md_path.read_text()


class TestPaths:
    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_path = tmp_path / "custom.md"
        monkeypatch.setenv("SEAHORSE_CLAUDE_MD", str(env_path))
        assert claude_md_path() == env_path

    def test_default_is_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEAHORSE_CLAUDE_MD", raising=False)
        assert claude_md_path() == Path.home() / ".claude" / "CLAUDE.md"

    def test_installed_false_on_missing_file(
        self, md_path: Path
    ) -> None:
        assert not installed()