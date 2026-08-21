"""Tests for the reproducible balanced subsample util (LMEB-S subsample 100).

The 2026-08-07 LMEB-S subsample decision (100 questions: 40 temporal-reasoning
+ 30 knowledge-update + 20 multi-session + 10 single-session-user, label
``subsampled_lmeb_s``) is materialized as a deterministic utility. The selection
is seed-fixed and ``split_hash`` is recomputed over the SUBSAMPLED instances so
the fingerprint identifies the subsample, not the full corpus (honesty).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance
from seahorse.benchmark.experiments.subsample import (
    SUBSAMPLE_COMPOSITION,
    SUBSAMPLE_LABEL,
    SUBSAMPLE_SEED,
    is_subsampled,
    subsample_dataset,
)

_TYPES = ("temporal-reasoning", "knowledge-update", "multi-session",
          "single-session-user", "single-session-assistant")


def _instance(i: int, qtype: str) -> BenchmarkInstance:
    return BenchmarkInstance(
        instance_id=f"q-{qtype}-{i}",
        question=f"question {qtype} {i}",
        golden_answer="42",
        golden_session_ids=(f"s-{qtype}-{i}",),
        golden_evidence=(),
        question_type=qtype,
        capabilities=(),
        cognitive_category="semantic",
        question_date=datetime(2026, 1, 1, tzinfo=UTC),
        haystack=(),
    )


def _dataset(per_type_counts: dict[str, int]) -> BenchmarkDataset:
    instances = []
    for qtype, n in per_type_counts.items():
        instances.extend(_instance(i, qtype) for i in range(n))
    return BenchmarkDataset(
        name="fake-lmeb",
        version="1.0.0",
        config="s",
        split_hash="full-corpus-hash",
        loader_code_sha256="loader",
        instances=tuple(instances),
        metadata={"total_questions": len(instances)},
    )


class TestSubsampleComposition:
    def test_selects_exact_quota_per_type(self) -> None:
        ds = _dataset(
            {
                "temporal-reasoning": 50,
                "knowledge-update": 50,
                "multi-session": 50,
                "single-session-user": 50,
                "single-session-assistant": 50,
            }
        )
        sub = subsample_dataset(ds)
        assert len(sub.instances) == sum(SUBSAMPLE_COMPOSITION.values()) == 100
        counts: dict[str, int] = {}
        for inst in sub.instances:
            counts[inst.question_type] = counts.get(inst.question_type, 0) + 1
        assert counts == dict(SUBSAMPLE_COMPOSITION)

    def test_excludes_types_not_in_composition(self) -> None:
        # single-session-assistant has NO quota — its instances must never be
        # selected even when the dataset carries them.
        ds = _dataset(
            {
                "temporal-reasoning": 50,
                "knowledge-update": 50,
                "multi-session": 50,
                "single-session-user": 50,
                "single-session-assistant": 50,
            }
        )
        sub = subsample_dataset(ds)
        assert all(
            i.question_type != "single-session-assistant" for i in sub.instances
        )
        assert len(sub.instances) == 100

    def test_selected_instances_come_from_the_dataset(self) -> None:
        ds = _dataset(
            {
                "temporal-reasoning": 50,
                "knowledge-update": 50,
                "multi-session": 50,
                "single-session-user": 50,
            }
        )
        sub = subsample_dataset(ds)
        selected_ids = {i.instance_id for i in sub.instances}
        assert all(i.instance_id in selected_ids for i in sub.instances)
        # Every selected instance comes from the dataset (selected ⊆ dataset).
        dataset_ids = {i.instance_id for i in ds.instances}
        assert selected_ids <= dataset_ids
        assert len(selected_ids) == len(sub.instances)  # no dup


class TestDeterminism:
    def test_same_seed_identical_selection_and_hash(self) -> None:
        ds = _dataset(
            {
                "temporal-reasoning": 50,
                "knowledge-update": 50,
                "multi-session": 50,
                "single-session-user": 50,
            }
        )
        a = subsample_dataset(ds, seed=SUBSAMPLE_SEED)
        b = subsample_dataset(ds, seed=SUBSAMPLE_SEED)
        assert a.split_hash == b.split_hash
        assert {i.instance_id for i in a.instances} == {i.instance_id for i in b.instances}

    def test_different_seed_different_selection(self) -> None:
        # Two seeds over pools large enough that the selections differ with
        # certainty (40 drawn from 80 of each type).
        ds = _dataset(
            {
                "temporal-reasoning": 80,
                "knowledge-update": 80,
                "multi-session": 80,
                "single-session-user": 80,
            }
        )
        a = subsample_dataset(ds, seed=1)
        b = subsample_dataset(ds, seed=2)
        assert {i.instance_id for i in a.instances} != {i.instance_id for i in b.instances}


class TestSplitHashHonesty:
    def test_hash_recomputed_over_subsample(self) -> None:
        ds = _dataset(
            {
                "temporal-reasoning": 50,
                "knowledge-update": 50,
                "multi-session": 50,
                "single-session-user": 50,
            }
        )
        sub = subsample_dataset(ds)
        # The subsample fingerprint must NOT equal the full-corpus hash — a
        # full-corpus hash on a 24%-of-corpus slice would be a mislabeled
        # fingerprint (fail-loud honesty).
        assert sub.split_hash != ds.split_hash
        assert sub.split_hash != "full-corpus-hash"
        assert len(sub.split_hash) == 64


class TestFailLoud:
    def test_insufficient_instances_raises(self) -> None:
        ds = _dataset(
            {
                "temporal-reasoning": 10,  # needs 40
                "knowledge-update": 50,
                "multi-session": 50,
                "single-session-user": 50,
            }
        )
        with pytest.raises(ValueError, match="temporal-reasoning"):
            subsample_dataset(ds)

    def test_missing_type_raises(self) -> None:
        ds = _dataset({"temporal-reasoning": 50})  # knowledge-update absent
        with pytest.raises(ValueError, match="knowledge-update"):
            subsample_dataset(ds)


class TestMetadata:
    def test_metadata_proves_what_was_evaluated(self) -> None:
        ds = _dataset(
            {
                "temporal-reasoning": 50,
                "knowledge-update": 50,
                "multi-session": 50,
                "single-session-user": 50,
            }
        )
        sub = subsample_dataset(ds)
        assert sub.metadata["subsampled"] is True
        assert sub.metadata["subsample_seed"] == SUBSAMPLE_SEED
        assert sub.metadata["subsample_label"] == SUBSAMPLE_LABEL
        assert sub.metadata["subsample_total"] == 100
        assert sub.metadata["full_total"] == len(ds.instances)
        assert sub.metadata["subsample_composition"] == dict(SUBSAMPLE_COMPOSITION)
        assert sub.name.endswith(SUBSAMPLE_LABEL)
        assert is_subsampled(sub) is True
        assert is_subsampled(ds) is False
