"""Seahorse Persistence Layer.

Storage-agnostic front: Protocols live in ``seahorse.contracts.persistence``;
SQLite implementations live here. The current release ships the relational
repos; the vec0 and FTS5 implementations are deferred to a later release
(Protocol stubs raise NotImplementedError).
"""

__all__: list[str] = []
