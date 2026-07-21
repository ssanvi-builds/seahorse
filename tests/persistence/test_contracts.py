"""Validate the contracts module: episode/engine/persistence shapes and Protocol
signatures match the signed contracts (SO-1, SO-2, SO-3, SO-7, SO-8b).
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from seahorse.contracts import (
    AuditEvent,
    Episode,
    EpisodeIndexRepository,
    EpisodeRepository,
    FtsDoc,
    ReindexJob,
)
from seahorse.contracts.persistence import (
    EmbeddingsCacheRepository,
    FullTextIndexRepository,
    ReindexJobRepository,
    SidecarIndexRepository,
    VectorIndexRepository,
)


def test_episode_has_signed_field_set():
    # SO-2 superset: #3 ships the canonical Pydantic model; the field set is a
    # superset of what #6 materialized (adds supersedes_reason, the portable
    # frontmatter key from f5-03 §12.3).
    names = set(Episode.model_fields)
    expected = {
        "id",
        "created_at",
        "schema_version",
        "provenance",
        "body",
        "subject",
        "fact_id",
        "valid_at",
        "invalid_at",
        "expired_at",
        "supersedes",
        "supersedes_reason",
        "cognitive_type",
        "source_type",
        "title",
        "summary",
        "tags",
    }
    assert names == expected


def test_episode_body_is_excluded_from_dump():
    # f5-03 §5.8: body is Optional (str | None, default None) and exclude=True.
    # parse_file (#3) constructs an Episode from frontmatter WITHOUT body (body
    # is not in the YAML); hydrate attaches it lazily via model_copy. The DDL
    # NOT NULL on body_md is enforced at storage write, not at construction.
    # The wire serializers read body via getattr so it still travels #13/#14's
    # wire; model_dump (the YAML round-trip) omits it.
    body_field = Episode.model_fields["body"]
    assert body_field.exclude is True
    assert body_field.is_required() is False  # Optional: defaults to None
    ep = Episode(
        id="e1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={},
        body="# body",
    )
    assert "body" not in ep.model_dump(mode="json")
    assert ep.body == "# body"  # getattr still reads it
    # parse_file-style construction without body is valid (lazy hydration):
    ep_no_body = Episode(
        id="e2",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={},
    )
    assert ep_no_body.body is None
    assert "body" not in ep_no_body.model_dump(mode="json")


def test_episode_provenance_json_serializes_sorted():
    ep = Episode(
        id="e1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"b": 1, "a": 2},
        body="# body",
    )
    s = ep.provenance_json()
    assert s == '{"a":2,"b":1}'


def test_episode_is_frozen():
    ep = Episode(
        id="e1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={},
        body="# body",
    )
    # Pydantic frozen=True raises ValidationError (frozen_instance) on setattr.
    with pytest.raises(ValidationError):
        ep.id = "x"  # type: ignore[misc]


def test_episode_model_copy_preserves_frozen():
    # The engine mutates via model_copy (immutable update), not in-place.
    ep = Episode(
        id="e1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={},
        body="# body",
    )
    ep2 = ep.model_copy(update={"subject": "Sergio"})
    assert ep2.subject == "Sergio"
    assert ep.subject is None  # original untouched (immutability)
    assert ep2.id == ep.id  # unchanged fields preserved
    with pytest.raises(ValidationError):
        ep2.id = "y"  # type: ignore[misc]


def test_audit_event_has_eleven_fields():
    # SO-3c: AuditEvent inferred from audit_events DDL (11 columns).
    names = {f.name for f in dataclasses.fields(AuditEvent)}
    expected = {
        "primitive",
        "target_id",
        "transaction_time",
        "result",
        "agent_id",
        "session_id",
        "successor_id",
        "valid_time",
        "reason",
        "cognitive_type",
    }
    # Note: AuditEvent has 10 dataclass fields; the 11th column (id) is the
    # autoincrement PK, not part of the type. The 10 fields map the 10
    # non-PK audit_events columns.
    assert names == expected


def test_episode_repository_protocol_methods_present():
    # The Protocol surface #6 must implement. NO delete / update_body.
    methods = {
        "append",
        "set_invalid_at",
        "get",
        "find_vigent_by_fact_id",
        "chain_from",
        "query_vigent",
        "query_state_at",
        "query_known_at",
        "atomic",
    }
    for m in methods:
        assert hasattr(EpisodeRepository, m), f"missing {m}"
    assert not hasattr(EpisodeRepository, "delete")
    assert not hasattr(EpisodeRepository, "update_body")
    assert not hasattr(EpisodeRepository, "update_valid_at")


def test_episode_index_repository_has_seven_accessors_and_bfs():
    # SO-1 (7 accessors) + SO-8b (bfs_neighbors_state_at).
    so1 = {
        "get_rows",
        "get_rows_state_at",
        "get_rows_known_at",
        "chain_rows_from",
        "find_vigent_row_by_fact_id",
        "range_rows_state_at",
        "range_rows_known_at",
    }
    for m in so1:
        assert hasattr(EpisodeIndexRepository, m), f"missing SO-1 accessor {m}"
    assert hasattr(EpisodeIndexRepository, "bfs_neighbors_state_at")
    # bfs signature: keyword-only pit_kind, hops, include_tags_soft after *
    sig = inspect.signature(EpisodeIndexRepository.bfs_neighbors_state_at)
    params = sig.parameters
    assert "pit_kind" in params
    assert "hops" in params
    assert "include_tags_soft" in params
    assert params["pit_kind"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["hops"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["include_tags_soft"].kind is inspect.Parameter.KEYWORD_ONLY


def test_vector_index_repository_upsert_is_folded():
    # SO-7b: model_identity/content_hash/embedded_at folded into upsert kwargs.
    sig = inspect.signature(VectorIndexRepository.upsert)
    params = sig.parameters
    assert "model_identity" in params
    assert "content_hash" in params
    assert "embedded_at" in params
    assert "dim" in params
    assert params["model_identity"].kind is inspect.Parameter.KEYWORD_ONLY
    # NO write_meta method (rejected alternative 7b-i).
    assert not hasattr(VectorIndexRepository, "write_meta")
    assert hasattr(VectorIndexRepository, "distinct_model_identities")


def test_embeddings_cache_repository_keyed_by_model_role_hash():
    sig_lookup = inspect.signature(EmbeddingsCacheRepository.batch_lookup)
    assert set(sig_lookup.parameters) >= {"model_identity", "role", "content_hashes"}
    sig_insert = inspect.signature(EmbeddingsCacheRepository.batch_insert)
    assert set(sig_insert.parameters) >= {"model_identity", "role", "content_hashes", "vectors"}


def test_reindex_job_repository_methods():
    for m in ("create", "start", "pause", "finish", "fail", "list"):
        assert hasattr(ReindexJobRepository, m), f"missing {m}"


def test_sidecar_repository_typed_methods_no_raw_sql():
    for m in ("put_path", "get_path", "reindex"):
        assert hasattr(SidecarIndexRepository, m), f"missing {m}"
    # No method accepts a raw SQL predicate string.
    for _name, member in inspect.getmembers(SidecarIndexRepository, predicate=inspect.isfunction):
        sig = inspect.signature(member)
        assert "predicate_sql" not in sig.parameters


def test_full_text_index_repository_methods():
    for m in (
        "upsert",
        "search",
        "search_state_at",
        "search_known_at",
        "remove_for_rebuild",
        "rebuild",
        "count",
    ):
        assert hasattr(FullTextIndexRepository, m), f"missing {m}"


def test_reindex_job_dataclass_fields():
    names = {f.name for f in dataclasses.fields(ReindexJob)}
    assert names == {
        "job_id",
        "model_from",
        "model_to",
        "total",
        "done",
        "status",
        "started_at",
        "finished_at",
    }


def test_fts_doc_tags_default_empty_list():
    doc = FtsDoc(ep_id="e1", body_md="body")
    assert doc.tags == []
