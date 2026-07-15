"""Engine error vocabulary (owned by #2).

A single ``EngineError`` carries a stable ``code`` string plus optional
``context`` dict so callers (and #12/#13 facades) can ``match`` on the code
without parsing the message. Codes are the fail-loud contract: SO-3b
collision, I5 PENDING invalidation, supersedes dangling, I2 valid_at guard,
the #5 skip-path border, and the MVP-1 stub marker.
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
# I4: the decay feature (expired_at non-null) is not available in MVP-0. Named
# per f5-02 §8.2 (ExpiredAtNotSupportedError -> code E_EXPIRED_AT_NON_NULL).
E_EXPIRED_AT_NON_NULL = "E_EXPIRED_AT_NON_NULL"
# Defensive/backstop codes — unreachable in correct MVP-0 flow; fire on engine
# contract violation or on a non-null bi-temporal pair that violates I5.
E_CREATED_AT_ENGINE_OWNED = "E_CREATED_AT_ENGINE_OWNED"
E_MONOTONICITY_VIOLATED = "E_MONOTONICITY_VIOLATED"