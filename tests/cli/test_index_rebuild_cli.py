"""End-to-end ``seahorse index rebuild`` via the invoke harness.

Orchestrates ``frontmatter.rebuild.rebuild_from_vault`` over the vault's ``.md``
notes + reports ``RebuildReport`` / ``RebuildConflict``. Fail-loud honesty:
conflicts → exit 94 (``CLI_REBUILD_CONFLICTS``, split from the 75 overload); a
parse failure → domain error ``E_FRONTMATTER_INVALID`` (exit 90).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from seahorse.cli.exit_codes import CLI_NOT_IN_MVP_0
from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import serialize
from tests.cli.conftest import invoke


def _uuid7(suffix: str) -> str:
    return f"01234567-89ab-7def-8123-456789abcde{suffix}"


def _write_note(
    vault: Path,
    name: str,
    *,
    ep_id: str,
    title: str | None = None,
) -> Path:
    ep = Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"agent_id": "seahorse/test", "extraction_mode": "skip"},
        body=f"# {name}\nbody.\n",
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        cognitive_type="fact",
        source_type="agent",
        title=title if title is not None else name,
        summary=f"summary {name}",
    )
    path = vault / f"{name}.md"
    serialize(ep, path, exclude_none=True)
    return path


def test_index_rebuild_clean(tmp_path, vault):
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _write_note(vault, "paris", ep_id=_uuid7("02"))
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["command"] == "index rebuild"
    assert obj["indexed"] == 2
    assert obj["skipped"] == 0
    assert obj["conflicts"] == []


def test_index_rebuild_human_clean(tmp_path, vault):
    _write_note(vault, "solo", ep_id=_uuid7("01"))
    code, out, err = invoke(["--vault", str(vault), "index", "rebuild"])
    assert code == 0, err
    assert "indexed" in out


def test_index_rebuild_empty_vault_clean(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["indexed"] == 0
    assert obj["skipped"] == 0


def test_index_rebuild_conflicts_exit_94(tmp_path, vault):
    _write_note(vault, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(vault, "c2", ep_id=_uuid7("02"), title="same-subject")
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 94, err
    # The report is on stdout (operator sees the conflict list)...
    obj = json.loads(out)
    assert obj["indexed"] == 0
    assert obj["skipped"] == 2
    # ...and the error envelope is on stderr with the CLI_REBUILD_CONFLICTS code.
    assert "CLI_REBUILD_CONFLICTS" in err


def test_index_rebuild_conflicts_exit_distinct_from_reserved(tmp_path, vault):
    # Rebuild conflicts exit 94 is DISTINCT from the reserved-stub exit 75
    # (CLI_NOT_IN_MVP_0). Before the split they shared 75 and were only
    # disambiguated by the symbolic cli_code; now the int itself differs.
    _write_note(vault, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(vault, "c2", ep_id=_uuid7("02"), title="same-subject")
    code, out, err = invoke(["--vault", str(vault), "index", "rebuild"])
    assert code == 94, err
    assert code != CLI_NOT_IN_MVP_0
    assert "CLI_REBUILD_CONFLICTS" in err
    assert "reserved in the current release" not in err


def test_index_rebuild_unparseable_note_is_cat_a_90(tmp_path, vault):
    _write_note(vault, "good", ep_id=_uuid7("01"))
    (vault / "raw.md").write_text("# no frontmatter\njust body.\n", encoding="utf-8")
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 90, err
    assert "E_FRONTMATTER_INVALID" in err
    assert "seahorse_code" in err


def test_index_rebuild_quiet_still_exits_94_on_conflicts(tmp_path, vault):
    _write_note(vault, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(vault, "c2", ep_id=_uuid7("02"), title="same-subject")
    code, out, err = invoke(["--vault", str(vault), "--quiet", "index", "rebuild"])
    assert code == 94, err
    # --quiet suppresses stdout (no report), but the error still hits stderr.
    assert out == ""
    assert "CLI_REBUILD_CONFLICTS" in err


def test_index_rebuild_is_no_longer_the_reserved_stub(tmp_path, vault):
    # Regression guard: `index rebuild` used to exit 75 with CLI_NOT_IN_MVP_0.
    # Now it runs for real; only `index verify` remains the reserved stub.
    _write_note(vault, "solo", ep_id=_uuid7("01"))
    code, out, err = invoke(["--vault", str(vault), "index", "rebuild"])
    assert code == 0, err
    assert "CLI_NOT_IN_MVP_0" not in err


def test_index_verify_still_reserved_exit_75(tmp_path, vault):
    # `index verify` remains an honest stub (needs vec0 from the embedder) —
    # fail-loud honesty.
    code, out, err = invoke(["--vault", str(vault), "index", "verify"])
    assert code == CLI_NOT_IN_MVP_0, err
    assert "CLI_NOT_IN_MVP_0" in err
    assert "reserved in the current release" in err


# --- vector/FTS backfill over the rebuilt index ------------------------------


def test_index_rebuild_backfill_skipped_without_embedder(monkeypatch, tmp_path, vault):
    # No embedder available -> honest skip (the listing regime). Forced
    # explicitly: with the ``embeddings`` extra installed the real fastembed
    # backend resolves, so the unavailable path is pinned via a None embedder.
    import seahorse.cli.vault_ops as vo

    monkeypatch.setattr(vo, "_try_build_passage_embedder", lambda: None)
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["backfill"] == "skipped (embedder unavailable)"


def test_index_rebuild_backfill_embeds_with_embedder(monkeypatch, tmp_path, vault):
    # With an embedder available, the rebuild backfills vec0 + FTS from the .md
    # bodies (best-effort; the episode_index rebuild is the primary op).
    import numpy as np

    from seahorse.embeddings.types import ModelIdentity

    class _FakeEmbedder:
        dim = 384

        async def embed(self, texts, role):
            return np.ones((len(texts), 384), dtype=np.float32)

        def model_identity(self) -> ModelIdentity:
            return ModelIdentity(
                backend="test", model_name="m", revision="r",
                dim=384, quantization="fp32", normalized=True,
            )

    import seahorse.cli.vault_ops as vo

    monkeypatch.setattr(vo, "_try_build_passage_embedder", lambda: _FakeEmbedder())
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _write_note(vault, "paris", ep_id=_uuid7("02"))
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["backfill"] == "2 episodes embedded"

    # verify the index actually populated (reopen the storage).
    from seahorse.cli.config import load_config
    from seahorse.persistence.storage import Storage

    cfg = load_config(vault)
    s = Storage(cfg.db_path)
    try:
        assert s.vector.count() == 2
        assert s.fts.count() == 2
    finally:
        s.close()


# --- P1b: human-edit divergence proposals -------------------------------------


def _seed_live_episode(vault: Path, ep_id: str, *, invalid_at=None) -> None:
    """Seed the live episode row the engine would have written pre-edit."""
    from seahorse.cli.config import load_config
    from seahorse.persistence.storage import Storage

    cfg = load_config(vault)
    ep = Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={
            "agent_id": "seahorse/test",
            "session_id": "sess-test",
            "source_type": "agent",
        },
        body="# madrid\nbody.\n",
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        invalid_at=invalid_at,
        cognitive_type="fact",
        source_type="agent",
        title="madrid",
        summary="summary madrid",
    )
    s = Storage(cfg.db_path)
    try:
        s.episodes.append(ep)
    finally:
        s.close()


def _human_edit_note(vault: Path, name: str) -> None:
    """Simulate the human correcting the note body in the vault (Obsidian)."""
    note = vault / f"{name}.md"
    note.write_text(
        note.read_text(encoding="utf-8") + "human corrected.\n",
        encoding="utf-8",
    )


def test_index_rebuild_reports_divergence_proposals(tmp_path, vault):
    # The note body was human-edited after the episode was remembered: the
    # report lists the divergence with the suggested improve — exit stays 0
    # (proposal-only: the rebuild NEVER auto-improves).
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _seed_live_episode(vault, _uuid7("01"))
    _human_edit_note(vault, "madrid")
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["divergences"] == [
        {"ep_id": _uuid7("01"), "file_path": "madrid.md"}
    ]


def test_index_rebuild_divergence_human_render_suggests_improve(
    tmp_path, vault
):
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _seed_live_episode(vault, _uuid7("01"))
    _human_edit_note(vault, "madrid")
    code, out, err = invoke(["--vault", str(vault), "index", "rebuild"])
    assert code == 0, err
    assert "madrid.md" in out
    assert f"seahorse improve {_uuid7('01')}" in out
    assert "--reason correction" in out


def test_index_rebuild_no_divergence_for_invalidated_episode(tmp_path, vault):
    # An invalidated episode is EXPECTED to be superseded — its live body may
    # differ from the note. Proposals only fire for active episodes.
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _seed_live_episode(
        vault, _uuid7("01"), invalid_at=datetime(2026, 1, 5, tzinfo=UTC)
    )
    _human_edit_note(vault, "madrid")
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["divergences"] == []


def test_index_rebuild_no_divergence_when_bodies_still_equal(tmp_path, vault):
    # No human edit: the note body and the live episode agree — no proposals
    # (the comparison is on the canonical hash, not raw bytes).
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _seed_live_episode(vault, _uuid7("01"))
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["divergences"] == []