"""``OutputCache`` — SQLite cache keyed by content-hash.

The cache key is ``sha256(fingerprint.run_id : prompt_hash : params_hash)``:
a prompt bump or a config change invalidates the cache automatically.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


class OutputCache:
    """SQLite key-value cache for benchmark outputs."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT)"
        )

    def get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM cache WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)", (key, value)
        )
        self._conn.commit()

    @staticmethod
    def key(fingerprint_run_id: str, prompt_hash: str, params_hash: str) -> str:
        return hashlib.sha256(
            f"{fingerprint_run_id}:{prompt_hash}:{params_hash}".encode()
        ).hexdigest()

    def close(self) -> None:
        self._conn.close()


__all__ = ["OutputCache"]
