"""Persistence-layer exceptions.

Re-exported from ``seahorse.contracts.engine`` so the existing import path
``from seahorse.persistence.errors import ...`` keeps working.
"""

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.contracts.index import HopsCapExceeded

__all__ = ["HopsCapExceeded", "InvalidationConflictError", "NotFound"]
