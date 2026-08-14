"""``_deterministic_extract`` — the zero-LLM editorial fallback.

The skip path has two branches:

1. **Use-as-is** — the engine's ``is_valid_skip_path`` gate accepts the
   candidate episode; no editorial work is needed and no LLM is spent.
2. **Fallback** — the gate rejects (``E_SKIP_CONTRACT_VIOLATED``); the skip
   path degrades to ``_deterministic_extract``, a pure, zero-LLM editorial
   pass that derives a subject from ``title > first H1`` and re-checks the
   gate. ``RememberPayload`` carries no filesystem path, so there is **no
   filename fallback** (unlike ``frontmatter.subject.derive_subject``, which
   is used by the migrator where a path exists).

``_deterministic_extract`` returns an ``ExtractedCandidate`` carrying ONLY
editorial fields (``subject`` / ``body`` / ``valid_at`` / ``cognitive_type`` /
``schema_version`` / ``tags``). Provenance (``extraction_mode`` /
``model_used`` / ``prompt_hash`` / ``confidence``) is built separately by
``run_skip_path`` — no field duplication. Engine-owned fields (``created_at`` /
``invalid_at`` / ``expired_at`` / ``id``) are never set here; the engine owns
them at write time. ``supersedes`` is write-path-owned (``None`` in
``remember``; the engine sets it only on improve/forget), so it is also absent
here and injected by ``run_skip_path``.

Loud failure: when neither ``title`` nor an H1 is present,
``SubjectDerivationError`` is raised rather than silently producing a
subject-less episode. Note: this fires ONLY in the fallback branch (gate
invalid) — the happy path preserves the engine's existing silent
``subject=None`` behavior (engine/collision.py).

References:
- seahorse/frontmatter/subject.py (raw_subject / normalize_subject)
- seahorse/facade/types.py (RememberPayload)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from seahorse.disclosure.types import SUMMARY_MAX_CHARS
from seahorse.facade.types import RememberPayload
from seahorse.frontmatter.subject import normalize_subject, raw_subject


class SubjectDerivationError(ValueError):
    """Raised when ``deterministic_extract`` cannot derive a subject.

    The skip-path fallback cannot produce a subject-less episode silently.
    When ``raw_subject`` finds neither a ``title`` nor a first H1 (and there is
    no filename fallback — ``RememberPayload`` carries no path), this is
    raised loud instead of appending with ``subject=None``.
    """

    def __init__(self, *, title: str | None, body: str) -> None:
        self.title = title
        self.body = body
        super().__init__(
            "deterministic_extract: cannot derive a subject (no title and no H1 "
            "in body); RememberPayload carries no path for a filename fallback"
        )


@dataclass(frozen=True)
class ExtractedCandidate:
    """Editorial-only result of ``deterministic_extract``.

    No provenance fields (built by ``run_skip_path``) and no engine-owned
    fields (set by the engine at write time). ``valid_at`` is passed through
    unsanitized — the engine enforces timezone-awareness in ``apply_fact``.
    """

    subject: str
    body: str
    valid_at: datetime | None
    cognitive_type: str | None
    schema_version: str
    tags: tuple[str, ...] = ()


def derive_summary(body: str, *, max_chars: int = SUMMARY_MAX_CHARS) -> str | None:
    """Deterministic summary fallback: first sentence of the body, skipping the
    H1, truncated to ``max_chars``.

    Zero-LLM: covers 100% of episodes including the skip path. The H1 is skipped
    so a tagged subject (``[session_tag:prompt_number]``) never contaminates the
    summary. Returns ``None`` when the body has no content after the H1 (honest
    — no invented summary).
    """
    content = _strip_h1(body)
    if not content:
        return None
    sentence = _first_sentence(content)
    if not sentence:
        return None
    return sentence[:max_chars]


def _strip_h1(body: str) -> str:
    """Drop a leading H1 line (and surrounding blank lines) from the body.

    The H1 is the subject (``title > H1 > None``); the summary must be the
    first sentence of the CONTENT, not the tagged subject line.
    """
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:]).strip()


def _first_sentence(text: str) -> str:
    """First sentence of ``text``: up to the first ``.``/``!``/``?`` followed by
    whitespace or end-of-text. Falls back to the whole text when no boundary is
    found (a single-sentence body)."""
    for i, ch in enumerate(text):
        if ch in ".!?" and (i + 1 >= len(text) or text[i + 1].isspace()):
            return text[: i + 1].strip()
    return text.strip()


def deterministic_extract(payload: RememberPayload) -> ExtractedCandidate:
    """Pure, zero-LLM editorial fallback of the skip path.

    Derives ``subject`` via ``title > first H1`` (no filename fallback —
    ``RememberPayload`` carries no path), normalized through
    ``frontmatter.subject.normalize_subject``. Raises ``SubjectDerivationError``
    loudly when neither is present. All other fields are passed through
    unsanitized (the engine enforces ``valid_at`` timezone-awareness).
    """
    raw = raw_subject(payload.title, payload.body)
    if raw is None:
        raise SubjectDerivationError(title=payload.title, body=payload.body)
    return ExtractedCandidate(
        subject=normalize_subject(raw),
        body=payload.body,
        valid_at=payload.valid_at,
        cognitive_type=payload.cognitive_type,
        schema_version=payload.schema_version,
        tags=payload.tags,
    )


__all__ = [
    "ExtractedCandidate",
    "SubjectDerivationError",
    "derive_summary",
    "deterministic_extract",
]