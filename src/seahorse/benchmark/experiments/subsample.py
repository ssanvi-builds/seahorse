"""Reproducible balanced subsample of a benchmark dataset — the harness util.

Materializes the 2026-08-07 LMEB-S subsample decision (100 questions: 40
temporal-reasoning + 30 knowledge-update + 20 multi-session + 10
single-session-user; label ``subsampled_lmeb_s``) as a deterministic utility so
any authoritative run reproduces the EXACT same 100 questions. The 2026-08-07
selection was made by hand and its seed was never recorded — the composition
was documented, the seed was not. This module fixes the seed and makes the
selection reproducible (a fixed seed is the minimum for a fingerprint-pinned
run; the composition is the documented contract).

The honest part: ``split_hash`` is recomputed over the SUBSAMPLED instances
(NOT copied from the full corpus), so the fingerprint identifies the SUBSAMPLE.
A full-corpus hash on a 24%-of-corpus slice would be a mislabeled fingerprint.
The metadata carries the composition + seed + label so a report can prove what
was actually evaluated.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict

from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance

# The documented 2026-08-07 composition (100 questions, balanced).
SUBSAMPLE_COMPOSITION: dict[str, int] = {
    "temporal-reasoning": 40,
    "knowledge-update": 30,
    "multi-session": 20,
    "single-session-user": 10,
}

# The fixed seed (the 2026-08-07 selection was manual; this pins the selection).
SUBSAMPLE_SEED = 42

# The honest label carried in the dataset name + metadata.
SUBSAMPLE_LABEL = "subsampled_lmeb_s"


def subsample_dataset(
    dataset: BenchmarkDataset,
    *,
    seed: int = SUBSAMPLE_SEED,
    per_type: dict[str, int] | None = None,
) -> BenchmarkDataset:
    """Deterministic balanced subsample by ``question_type`` under a fixed seed.

    Groups the instances by type, shuffles each group with
    ``random.Random(seed)`` (the SAME rng object drives the draws, so the
    selection is reproducible for a fixed dataset), takes the first ``n`` of
    each type, and sorts the selected instances by ``instance_id`` for a
    deterministic output order (the ``split_hash`` depends on that order).
    Fail-loud when a type cannot satisfy its quota (``ValueError``) — a
    silently-short subsample would make the slice decisions non-comparable.
    """
    quotas = dict(per_type or SUBSAMPLE_COMPOSITION)
    rng = random.Random(seed)
    by_type: dict[str, list[BenchmarkInstance]] = {}
    for inst in dataset.instances:
        by_type.setdefault(inst.question_type, []).append(inst)
    selected: list[BenchmarkInstance] = []
    for qtype, n in quotas.items():
        pool = by_type.get(qtype)
        if pool is None or len(pool) < n:
            raise ValueError(
                f"subsample quota {qtype}={n} cannot be satisfied "
                f"(the dataset has {len(pool or [])} instances of that type)"
            )
        pool = sorted(pool, key=lambda i: i.instance_id)  # stable pre-shuffle order
        rng.shuffle(pool)
        selected.extend(pool[:n])
    selected.sort(key=lambda i: i.instance_id)  # deterministic output order (the hash)
    instances = tuple(selected)
    split_hash = hashlib.sha256(
        json.dumps(
            [asdict(i) for i in instances], sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()
    return BenchmarkDataset(
        name=f"{dataset.name}-{SUBSAMPLE_LABEL}",
        version=dataset.version,
        config=dataset.config,
        split_hash=split_hash,
        loader_code_sha256=dataset.loader_code_sha256,
        instances=instances,
        metadata={
            **dataset.metadata,
            "subsampled": True,
            "subsample_seed": seed,
            "subsample_label": SUBSAMPLE_LABEL,
            "subsample_composition": dict(quotas),
            "subsample_total": len(instances),
            "full_total": len(dataset.instances),
        },
    )


def is_subsampled(dataset: BenchmarkDataset) -> bool:
    """Whether a dataset is a subsample (a report can prove what was evaluated)."""
    return bool(dataset.metadata.get("subsampled", False))


__all__ = [
    "SUBSAMPLE_COMPOSITION",
    "SUBSAMPLE_LABEL",
    "SUBSAMPLE_SEED",
    "is_subsampled",
    "subsample_dataset",
]
