"""Tests for ``seahorse.cli.skill_install`` — packaged agent skills.

Contract: idempotent install, stale Seahorse skills updated in place, a
foreign SKILL.md (no Seahorse marker) is NEVER touched by install or
remove, SEAHORSE_CLAUDE_SKILLS_DIR override, absent state degrades to
no-ops instead of raising.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seahorse.cli.skill_install import (
    SKILL_MARKER,
    SKILL_NAMES,
    install_skill,
    install_skills,
    remove_skill,
    remove_skills,
    skill_path,
    skill_state,
    skill_template,
    skills_dir,
)


@pytest.fixture()
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    monkeypatch.setenv("SEAHORSE_CLAUDE_SKILLS_DIR", str(root))
    return root


class TestPaths:
    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "custom-skills"
        monkeypatch.setenv("SEAHORSE_CLAUDE_SKILLS_DIR", str(root))
        assert skills_dir() == root
        assert skill_path("consolidate") == root / "consolidate" / "SKILL.md"

    def test_default_is_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEAHORSE_CLAUDE_SKILLS_DIR", raising=False)
        assert skills_dir() == Path.home() / ".claude" / "skills"

    def test_names_cover_consolidate(self) -> None:
        assert "consolidate" in SKILL_NAMES
        for name in SKILL_NAMES:
            assert skill_template(name).startswith("---\n")
            assert SKILL_MARKER in skill_template(name)


class TestState:
    def test_absent_when_no_file(self, skills_root: Path) -> None:
        assert skill_state("consolidate") == "absent"

    def test_ours_when_marker_present(self, skills_root: Path) -> None:
        install_skill("consolidate")
        assert skill_state("consolidate") == "ours"

    def test_foreign_when_no_marker(self, skills_root: Path) -> None:
        path = skill_path("consolidate")
        path.parent.mkdir(parents=True)
        path.write_text("# My own consolidate skill\n", encoding="utf-8")
        assert skill_state("consolidate") == "foreign"

    def test_unreadable_file_is_foreign_never_raises(
        self, skills_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = skill_path("consolidate")
        path.parent.mkdir(parents=True)
        path.write_text("x", encoding="utf-8")
        path.chmod(0o000)
        monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
        try:
            assert skill_state("consolidate") == "foreign"
        finally:
            path.chmod(0o644)


class TestInstall:
    def test_fresh_install_writes_template(self, skills_root: Path) -> None:
        ok, detail = install_skill("consolidate")
        assert ok
        assert "installed" in detail
        assert skill_path("consolidate").read_text(encoding="utf-8") == (
            skill_template("consolidate")
        )

    def test_idempotent_second_call_is_noop(self, skills_root: Path) -> None:
        install_skill("consolidate")
        before = skill_path("consolidate").read_text(encoding="utf-8")
        ok, detail = install_skill("consolidate")
        assert ok and "already" in detail
        assert skill_path("consolidate").read_text(encoding="utf-8") == before

    def test_stale_skill_updated_in_place(self, skills_root: Path) -> None:
        install_skill("consolidate")
        path = skill_path("consolidate")
        path.write_text(
            f"---\nname: consolidate\n---\n\n{SKILL_MARKER}\nold body\n", encoding="utf-8"
        )
        ok, detail = install_skill("consolidate")
        assert ok and "updated" in detail
        assert path.read_text(encoding="utf-8") == skill_template("consolidate")

    def test_foreign_skill_never_touched(self, skills_root: Path) -> None:
        path = skill_path("consolidate")
        path.parent.mkdir(parents=True)
        path.write_text("# user's own skill\n", encoding="utf-8")
        ok, detail = install_skill("consolidate")
        assert not ok
        assert "left untouched" in detail
        assert path.read_text(encoding="utf-8") == "# user's own skill\n"

    def test_install_skills_aggregates_all_names(self, skills_root: Path) -> None:
        results = install_skills()
        assert [name for name, _, _ in results] == list(SKILL_NAMES)
        assert all(ok for _, ok, _ in results)
        for name in SKILL_NAMES:
            assert skill_state(name) == "ours"


class TestRemove:
    def test_remove_ours_deletes_file(self, skills_root: Path) -> None:
        install_skill("consolidate")
        ok, detail = remove_skill("consolidate")
        assert ok and "removed" in detail
        assert not skill_path("consolidate").exists()
        assert skill_state("consolidate") == "absent"

    def test_remove_empty_parent_dir(self, skills_root: Path) -> None:
        install_skill("consolidate")
        remove_skill("consolidate")
        assert not skill_path("consolidate").parent.exists()

    def test_remove_foreign_never_touches(self, skills_root: Path) -> None:
        path = skill_path("consolidate")
        path.parent.mkdir(parents=True)
        path.write_text("# user's own skill\n", encoding="utf-8")
        ok, detail = remove_skill("consolidate")
        assert ok
        assert "left untouched" in detail
        assert path.read_text(encoding="utf-8") == "# user's own skill\n"

    def test_remove_when_absent_is_ok(self, skills_root: Path) -> None:
        ok, _ = remove_skill("consolidate")
        assert ok

    def test_remove_skills_aggregates_all_names(self, skills_root: Path) -> None:
        install_skills()
        results = remove_skills()
        assert [name for name, _, _ in results] == list(SKILL_NAMES)
        assert all(ok for _, ok, _ in results)
        for name in SKILL_NAMES:
            assert skill_state(name) == "absent"