"""LongMemEval adapter — the reference benchmark for the first release.

The raw LongMemEval JSON snapshot is parsed with the STDLIB ``json`` module —
no ``datasets`` loading script, no ``trust_remote_code`` (preferred
pre-materialization). Rationale (verified on the real S snapshot, 2026-08-07):
the ``datasets`` JSON pipeline breaks on this dataset under pyarrow 25 — the
mixed-type ``/answer`` column (468 str + 32 int) drives its schema retry to
double ``block_size`` until it overflows ``int32``. The raw file resolves via
``huggingface_hub.hf_hub_download`` (the cached blob — same hub cache the
``datasets`` loader would use), avoiding the remote-code execution entirely.

The canonicalization is the audit target: ``loader_code_sha256`` is the SHA-256
of THIS module's source, so any mapping change invalidates the fingerprint.
Tests use synthetic rows via ``_from_row`` (no download in CI).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from seahorse.benchmark.adapters.base import parse_date
from seahorse.benchmark.adapters.registry import AdapterRegistry
from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance

_LMEB_QUESTION_TYPE_TO_COGNITIVE: dict[str, str] = {
    "single-session-user": "episodic",
    "single-session-assistant": "episodic",
    "single-session-preference": "semantic",
    "multi-session": "semantic",
    "knowledge-update": "semantic",
    "temporal-reasoning": "semantic",
    "abstention": "n/a",
    # NOTE: "procedural" is NOT covered by LongMemEval-S; declared as a gap.
}

_LMEB_CAPABILITY_MAP: dict[str, tuple[str, ...]] = {
    "single-session-user": ("information-extraction",),
    "single-session-assistant": ("information-extraction",),
    "single-session-preference": ("information-extraction",),
    "multi-session": ("multi-session-reasoning",),
    "knowledge-update": ("knowledge-update",),
    "temporal-reasoning": ("temporal-reasoning",),
    "abstention": ("abstention",),
}

# The LongMemEval S/M/oracle JSON files ship as one dataset with per-size
# splits; the adapter resolves a config ("s") to its raw JSON split name
# (the HF file is ``<split>.json``).
_CONFIG_TO_SPLIT: dict[str, str] = {
    "s": "longmemeval_s_cleaned",
    "m": "longmemeval_m_cleaned",
}


@AdapterRegistry.register("lmeb")
class LMEBLoader:
    """LongMemEval adapter — stdlib-json canonicalization of the raw snapshot."""

    _DATASET_HF_REPO = "xiaowu0162/longmemeval-cleaned"
    _DATASET_VERSION = "1.0.0"

    @staticmethod
    def name() -> str:
        return "LongMemEval"

    @staticmethod
    def available_configs() -> tuple[str, ...]:
        return ("s",)  # the first release: only "s"

    @staticmethod
    def _split_name(config: str) -> str:
        """The HF split for a config (``s`` → ``longmemeval_s_cleaned``).

        The raw JSON file on the hub is ``<split>.json``.
        """
        if config not in _CONFIG_TO_SPLIT:
            raise ValueError(
                f"unknown LongMemEval config: {config!r} "
                f"(expected {sorted(_CONFIG_TO_SPLIT)})"
            )
        return _CONFIG_TO_SPLIT[config]

    @staticmethod
    def load(config: BenchmarkConfig) -> BenchmarkDataset:
        raw_path = _resolve_raw_json_path(config)
        with open(raw_path, encoding="utf-8") as f:
            rows = json.load(f)
        instances = tuple(LMEBLoader._from_row(row) for row in rows)
        split_hash = hashlib.sha256(
            json.dumps(
                [asdict(i) for i in instances], sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        return BenchmarkDataset(
            name=f"longmemeval-{config.dataset_config}",
            version=LMEBLoader._DATASET_VERSION,
            config=config.dataset_config,
            split_hash=split_hash,
            loader_code_sha256=_loader_code_sha256(),
            instances=instances,
            metadata={
                "total_questions": len(instances),
                "hf_repo": LMEBLoader._DATASET_HF_REPO,
                "raw_file": _CONFIG_TO_SPLIT[config.dataset_config],
                "materialization": "stdlib_json_no_trust_remote_code",
            },
        )

    @staticmethod
    def _from_row(row: dict) -> BenchmarkInstance:
        """Map a raw LongMemEval row to a canonical ``BenchmarkInstance``.

        The haystack is the triple ``(haystack_session_ids, haystack_dates,
        haystack_sessions)`` — sessions are parallel arrays of turn-lists. The
        canonical session shape the corpus consumes is ``{session_id, date,
        turns: [{body, ...}]}``.
        """
        q_type = row["question_type"]
        is_abstention = q_type == "abstention" or str(row.get("question_id", "")).endswith(
            "_abs"
        )
        answer = row.get("answer")
        return BenchmarkInstance(
            instance_id=str(row["question_id"]),
            question=row["question"],
            # LongMemEval answers are mixed (468 str + 32 int); the canonical
            # ``golden_answer: str | None`` contract normalizes to str.
            golden_answer=None if answer is None else str(answer),
            golden_session_ids=tuple(row.get("answer_session_ids", [])),
            golden_evidence=(),
            question_type=q_type,
            capabilities=_LMEB_CAPABILITY_MAP.get(q_type, ()),
            cognitive_category=_LMEB_QUESTION_TYPE_TO_COGNITIVE.get(q_type, "n/a"),
            question_date=parse_date(row.get("question_date")),
            haystack=LMEBLoader._canonicalize_sessions(row),
            abstention=is_abstention,
        )

    @staticmethod
    def _canonicalize_sessions(row: dict) -> tuple[dict, ...]:
        """Zip the parallel ``session_ids``/``dates``/``sessions`` arrays into
        the canonical ``{session_id, date, turns}`` shape."""
        ids = row.get("haystack_session_ids", [])
        dates = row.get("haystack_dates", [])
        sessions = row.get("haystack_sessions", [])
        canon: list[dict] = []
        for i, turns in enumerate(sessions):
            session_id = ids[i] if i < len(ids) else f"session_{i}"
            date = parse_date(dates[i]) if i < len(dates) else None
            canon.append(
                {
                    "session_id": session_id,
                    "date": date,
                    # Empty turns (10/199,509 in LMEB-S) are dropped — the write
                    # path raises E_EMPTY_BODY on a blank body.
                    "turns": tuple(
                        {
                            "body": content,
                            # Conversational turns have no H1; the skip path's
                            # subject derivation (title > H1 > None) needs a
                            # title or it raises. A truncated content prefix is
                            # a meaningful, mostly-distinct subject.
                            "title": _turn_title(content),
                        }
                        for t in turns
                        if (content := t.get("content", "")).strip()
                    ),
                }
            )
        return tuple(canon)


_TITLE_PREFIX_CHARS = 60


def _turn_title(content: str) -> str:
    """A derivable subject for a conversational turn (no H1 in LMEB content).

    The skip path's ``deterministic_extract`` raises ``SubjectDerivationError``
    when neither a title nor an H1 is present — the corpus builder must ensure a
    derivable subject. A whitespace-collapsed content prefix is a meaningful,
    mostly-distinct subject; the empty fallback keeps the turn ingestible.
    """
    cleaned = " ".join(content.split())
    return cleaned[:_TITLE_PREFIX_CHARS] or "untitled"


def _resolve_raw_json_path(config: BenchmarkConfig) -> Path:
    """Locate the raw LongMemEval JSON snapshot (cached hub blob, no remote code).

    Lazy ``huggingface_hub`` import — keeps ``import seahorse.benchmark``
    importable without the ``benchmark`` extra; the RuntimeError is the same
    gate the skeleton specifies for a missing extra.
    """
    import importlib

    try:
        hub = importlib.import_module("huggingface_hub")
    except ImportError as exc:
        raise RuntimeError(
            "install seahorse[benchmark] to load LongMemEval "
            "(huggingface_hub via the datasets extra)"
        ) from exc
    return Path(
        hub.hf_hub_download(
            LMEBLoader._DATASET_HF_REPO,
            f"{_CONFIG_TO_SPLIT[config.dataset_config]}.json",
            repo_type="dataset",
        )
    )


def _loader_code_sha256() -> str:
    """The canonicalization code hash — the trust_remote_code audit target now
    that no remote script executes (prematerialization preference)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _materialize_rows(raw_path: Path) -> list[dict[str, Any]]:
    """Read the raw JSON array (kept separate for the load-path test hook)."""
    with open(raw_path, encoding="utf-8") as f:
        return json.load(f)


__all__ = ["LMEBLoader", "_resolve_raw_json_path", "_loader_code_sha256"]
