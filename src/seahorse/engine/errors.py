"""Engine error vocabulary (owned by the engine).

A single ``EngineError`` carries a stable ``code`` string plus optional
``context`` dict so callers (and the facade and MCP-server layers) can ``match``
on the code without parsing the message. Codes are the fail-loud contract:
concurrent-subject collision, PENDING invalidation, dangling supersedes, the
valid_at guard, the write-path skip border, and the not-yet-released stub
marker.
"""

from __future__ import annotations

from typing import Any


class EngineError(Exception):
    """Engine fail-loud error identified by a stable ``code`` string."""

    def __init__(self, code: str, **context: Any) -> None:
        self.code = code
        self.context: dict[str, Any] = dict(context)
        super().__init__(f"{code}: {self.context}" if self.context else code)


# Stable code constants — unique strings, never renamed (callers match on them).
E_COLLISION_EXISTS = "E_COLLISION_EXISTS"
E_PENDING_CANNOT_INVALIDATE = "E_PENDING_CANNOT_INVALIDATE"
E_DANGLING_SUPERSEDES = "E_DANGLING_SUPERSEDES"
E_VALID_AT_HUMAN_ONLY = "E_VALID_AT_HUMAN_ONLY"
E_SKIP_CONTRACT_VIOLATED = "E_SKIP_CONTRACT_VIOLATED"
E_NOT_IN_MVP_0 = "E_NOT_IN_MVP_0"
# The decay feature (expired_at non-null) is not available in the first release.
E_EXPIRED_AT_NON_NULL = "E_EXPIRED_AT_NON_NULL"
# Defensive/backstop codes — unreachable in a correct current-release flow; fire
# on an engine contract violation or on a non-null bi-temporal pair that violates
# the monotonic ordering.
E_CREATED_AT_ENGINE_OWNED = "E_CREATED_AT_ENGINE_OWNED"
E_MONOTONICITY_VIOLATED = "E_MONOTONICITY_VIOLATED"