"""``_merge_known`` — preserve x-*/comments/quotes; only mutated fields change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from seahorse.frontmatter.adapter import _merge_known, parse_file, serialize
from tests.frontmatter.conftest import make_episode


def _cm(**kw: object) -> CommentedMap:
    cm = CommentedMap()
    cm.update(kw)
    return cm


class TestPreservation:
    def test_x_keys_preserved_on_write(self, vault: Path) -> None:
        ep = make_episode()
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        # inject an x-* key + an Obsidian field by hand
        text = p.read_text().replace(
            "---\n", "---\nx-seahorse-author-tool-version: 0.3.2\naliases:\n  - Reunión\n", 1
        )
        p.write_text(text)
        # mutate invalid_at and re-serialize
        _cm_baseline, _body, ep2 = parse_file(p)
        ep2m = ep2.model_copy(update={"invalid_at": datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)})
        serialize(ep2m, p, exclude_none=False)
        out = p.read_text()
        assert "x-seahorse-author-tool-version: 0.3.2" in out
        assert "Reunión" in out  # aliases preserved
        assert "invalid_at: " in out  # the mutation landed

    def test_top_level_yaml_comment_preserved(self, vault: Path) -> None:
        ep = make_episode()
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        text = p.read_text().replace("---\n", "---\n# header comment\n", 1)
        p.write_text(text)
        _cm, _body, ep2 = parse_file(p)
        serialize(ep2, p, exclude_none=True)
        assert "# header comment" in p.read_text()

    def test_unchanged_field_keeps_its_value(self, vault: Path) -> None:
        # Mutating only invalid_at must not alter title/tags/cognitive_type.
        ep = make_episode()
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        before = p.read_text()
        _cm, _body, ep2 = parse_file(p)
        ep2m = ep2.model_copy(update={"invalid_at": datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)})
        serialize(ep2m, p, exclude_none=False)
        after = p.read_text()
        # title, tags, cognitive_type lines are unchanged
        assert "title: Madrid" in after
        assert "- geo" in after
        assert "cognitive_type: semantic" in after
        # the only addition vs the first-release baseline is the explicit invalid_at null
        # becoming a value (and exclude_none=False adds nulls) — check invalid_at
        assert "invalid_at: '2026-07-17T12:00:00Z'" in after
        del before


class TestMergeInsertion:
    def test_new_key_inserted_at_canonical_position(self, vault: Path) -> None:
        # baseline has only id+created_at+schema_version+provenance; serializing
        # an episode that ALSO carries title/tags must insert those new keys in
        # canonical order (after provenance, before end), not alphabetically.
        ep = make_episode()  # has title="Madrid", tags=["geo"], etc.
        p = vault / "note.md"
        # minimal baseline file (no title/tags — those are new keys to insert)
        p.write_text(
            "---\n"
            f"id: {ep.id}\n"
            "created_at: '2026-07-16T12:00:00Z'\n"
            "schema_version: 0.1.0\n"
            "provenance:\n"
            "  agent_id: m\n"
            "  session_id: s\n"
            "  source_type: human\n"
            "  extraction_mode: skip\n"
            "---\n# Madrid\n"
        )
        # serialize the FULL episode onto the partial baseline: title/tags land
        # in canonical position (the baseline had neither).
        serialize(ep, p, exclude_none=True)
        out = p.read_text()
        # canonical order: id < created_at < schema_version < provenance < ... < title < tags.
        # Use value-bearing substrings so nested keys (agent_id) don't match.
        pos_id = out.index("id: " + ep.id)
        pos_prov = out.index("\nprovenance:")
        pos_title = out.index("title: Madrid")
        pos_tags = out.index("\ntags:")
        assert pos_id < pos_prov < pos_title < pos_tags

    def test_merge_into_empty_cm_inserts_all_dump_fields(self) -> None:
        ep = make_episode()
        dump = ep.model_dump(mode="json", exclude_none=True, exclude=set())
        cm = CommentedMap()
        _merge_known(cm, dump)
        assert "id" in cm
        assert "title" in cm
        assert cm["title"] == "Madrid"


class TestProvenanceRecursion:
    def test_nested_provenance_merged_not_replaced(self, vault: Path) -> None:
        ep = make_episode(
            provenance={
                "agent_id": "m", "session_id": "s",
                "source_type": "human", "extraction_mode": "skip",
            }
        )
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        _cm, _body, ep2 = parse_file(p)
        # mutate one provenance field (model_copy on the dict)
        new_prov = {**ep2.provenance, "session_id": "s2"}
        ep2m = ep2.model_copy(update={"provenance": new_prov})
        serialize(ep2m, p, exclude_none=True)
        _cm2, _body2, ep3 = parse_file(p)
        assert ep3.provenance["session_id"] == "s2"
        assert ep3.provenance["agent_id"] == "m"  # untouched field preserved