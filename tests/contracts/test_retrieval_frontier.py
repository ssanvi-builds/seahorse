"""Validate FusedCandidate — the hybrid-retrieval → indexer boundary.

``FusedCandidate`` is owned by hybrid retrieval and materialized in
``contracts.retrieval`` by the indexer (the first consumer to ship), mirroring
how ``IndexRowData`` is owned by the indexer and materialized by the persistence
layer. This test guards the frozen shape and the contract-surface re-export so
hybrid retrieval ships against a stable contract.
"""

from __future__ import annotations

import dataclasses

from seahorse.contracts import retrieval as retrieval_mod
from seahorse.contracts.retrieval import FusedCandidate


def test_fused_candidate_has_exactly_three_fields():
    assert len(dataclasses.fields(FusedCandidate)) == 3


def test_fused_candidate_field_names_match_signed_contract():
    names = {f.name for f in dataclasses.fields(FusedCandidate)}
    assert names == {"ep_id", "score", "sources"}


def test_fused_candidate_is_frozen():
    c = FusedCandidate(ep_id="e1", score=0.5, sources=("vector",))
    try:
        c.score = 0.9  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("FusedCandidate must be frozen")


def test_fused_candidate_sources_is_tuple():
    # sources is provenance (vector/bm25/bfs/chain), NOT a reranking signal.
    c = FusedCandidate(ep_id="e1", score=0.5, sources=("vector", "bm25"))
    assert isinstance(c.sources, tuple)
    assert c.sources == ("vector", "bm25")


def test_module_reexports_fused_candidate():
    assert retrieval_mod.FusedCandidate is FusedCandidate