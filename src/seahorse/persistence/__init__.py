"""Seahorse Persistence Layer.

SQLite implementations of the persistence contracts. The seams that earned
their keep (``EpisodeIndexRepository`` / ``VectorIndexRepository`` /
``FullTextIndexRepository`` — the interfaces the disclosure and retrieval
suites test against) live in ``seahorse.contracts.persistence``; the
single-adapter repos (episode, audit, sidecar, embeddings cache, reindex
jobs) are concrete here.
"""

__all__: list[str] = []
