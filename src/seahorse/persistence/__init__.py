"""Seahorse Persistence Layer (Component #6).

Storage-agnostic front: Protocols live in ``seahorse.contracts.persistence``;
SQLite implementations live here. MVP-0 ships the relational repos; the vec0
and FTS5 implementations are deferred to MVP-1 (Protocol stubs raise
NotImplementedError).
"""

__all__: list[str] = []
