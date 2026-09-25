"""Chunker — fixed windows over the EFFECTIVE embedded text (P3.2 seam).

A pure function of the same text ``RetrievalIndexer._embed_text`` produces
(``body+summary`` folds the summary in front, then the body) so chunking is
composable with ``embed_mode``: whatever the effective text is, the chunker
splits THAT. Whole-episode embeddings dilute an answer fragment across the
body's full vocabulary mass; a window concentrated on the fragment carries it
at full weight — the P3 hypothesis this seam makes measurable.

Why fixed character windows (the "simplest defensible" cut): no tokenizer
dependency (the fastembed backend already owns tokenization), deterministic
across runs and processes, and byte-stable for the same input text — the
``chunk_id = "{parent_ep_id}#{i}"`` mapping is reproducible. The window stays
comfortably inside the embedder's context budget (1200 chars ≈ 300 tokens for
English vs the 512-token mE5-small window); the overlap keeps a fragment that
straddles a boundary whole in at least one chunk (a distinctive answer
fragment is far shorter than the overlap).

The cap is an ingest-budget guard, documented honestly: beyond
``MAX_CHUNKS_PER_EPISODE`` the tail is NOT embedded (truncation, not
absorption — a final over-window chunk would be tokenizer-truncated by the
backend anyway, moving the same loss silently). 32 chunks ≈ 37K chars of
episode text is far beyond any session turn or vault note.
"""

from __future__ import annotations

# The fixed window (chars). Comfortably inside the embedder's token budget.
CHUNK_WINDOW_CHARS = 1200

# The overlap between consecutive windows (chars): a fragment straddling a
# boundary stays whole in at least one chunk.
CHUNK_OVERLAP_CHARS = 120

# The per-episode chunk cap (ingest-budget guard; the tail beyond is dropped).
MAX_CHUNKS_PER_EPISODE = 32


def chunk_text(text: str) -> list[str]:
    """Split the effective embedded text into deterministic windows.

    Returns ``[]`` for blank text (the indexer's no-body guard makes this
    unreachable in practice — kept honest for a pure function). A text within
    the window returns ``[text]`` (the single-chunk case, identical to the
    flag-off embedding surface). Longer texts walk fixed windows with the
    overlap step; the chunk list is capped at ``MAX_CHUNKS_PER_EPISODE``.
    """
    if not text or not text.strip():
        return []
    if len(text) <= CHUNK_WINDOW_CHARS:
        return [text]
    step = CHUNK_WINDOW_CHARS - CHUNK_OVERLAP_CHARS
    chunks: list[str] = []
    start = 0
    while start < len(text) and len(chunks) < MAX_CHUNKS_PER_EPISODE:
        end = min(start + CHUNK_WINDOW_CHARS, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return chunks


__all__ = [
    "CHUNK_WINDOW_CHARS",
    "CHUNK_OVERLAP_CHARS",
    "MAX_CHUNKS_PER_EPISODE",
    "chunk_text",
]