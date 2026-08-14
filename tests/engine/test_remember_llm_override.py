"""Tests for the LLM-path ``subject``/``tags`` override on ``remember``.

``remember`` accepts ``subject``/``tags`` additively. When the extractor
produced a subject it WINS over the default derivation (``title > H1 > None``)
in ``apply_fact``, and the tags are stored on the episode. The skip path never
passes them, so its derivation is byte-identical to the pre-override behaviour
— pinned here as a regression against the old ``fact_id_for(body, title)``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.engine.collision import fact_id_for, fact_id_of
from seahorse.engine.engine import BiTemporalEngine

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class TestRememberSubjectOverride:
    def test_explicit_subject_wins_over_derivation(self, storage) -> None:
        repo, audit = storage
        engine = BiTemporalEngine(repo=repo, audit=audit)
        res = engine.remember(
            body="# Manual H1 subject\ncontent",
            by={"source_type": "agent"},
            subject="llm inferred subject",
            now=_NOW,
        )
        ep = repo.get(res.ep_id)
        assert ep.subject == "llm inferred subject"
        assert ep.fact_id == fact_id_of("llm inferred subject")

    def test_skip_path_derivation_unchanged(self, storage) -> None:
        repo, audit = storage
        engine = BiTemporalEngine(repo=repo, audit=audit)
        res = engine.remember(
            body="# Manual H1 subject\ncontent",
            by={"source_type": "agent"},
            now=_NOW,
        )
        ep = repo.get(res.ep_id)
        # regression: same subject/fact_id as the pre-override derivation.
        assert ep.subject == "manual h1 subject"
        assert ep.fact_id == fact_id_for("# Manual H1 subject\ncontent")

    def test_no_subject_derivable_stays_none(self, storage) -> None:
        repo, audit = storage
        engine = BiTemporalEngine(repo=repo, audit=audit)
        res = engine.remember(
            body="no title and no h1 in here",
            by={"source_type": "agent"},
            now=_NOW,
        )
        ep = repo.get(res.ep_id)
        assert ep.subject is None
        assert ep.fact_id is None

    def test_explicit_subject_rescues_when_none_derivable(self, storage) -> None:
        repo, audit = storage
        engine = BiTemporalEngine(repo=repo, audit=audit)
        res = engine.remember(
            body="no title and no h1 in here",
            by={"source_type": "agent"},
            subject="llm rescued subject",
            now=_NOW,
        )
        ep = repo.get(res.ep_id)
        assert ep.subject == "llm rescued subject"
        assert ep.fact_id == fact_id_of("llm rescued subject")


class TestTagsNotPersisted:
    def test_sqlite_store_reads_tags_back_as_empty(self, storage) -> None:
        # Honesty: the SQLite episode store does NOT persist ``tags`` (the repo
        # reads them back as ``[]``). That is why ``remember`` does not accept a
        # tags override — an injected tag would be a silent lie. This pins the
        # current store behaviour so nobody reintroduces the parameter before
        # the persistence layer adds the column.
        repo, audit = storage
        engine = BiTemporalEngine(repo=repo, audit=audit)
        res = engine.remember(
            body="# Title\ncontent",
            by={"source_type": "agent"},
            now=_NOW,
        )
        ep = repo.get(res.ep_id)
        assert ep.tags == []
