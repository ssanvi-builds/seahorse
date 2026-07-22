"""Frontmatter adapter/migrator — the ``.md`` ↔ ``Episode`` codec (#3, F3.3).

Confinement (load-bearing): ``ruamel.yaml`` and ``python-frontmatter`` are
imported ONLY by ``seahorse.frontmatter.handler`` and ``seahorse.frontmatter.adapter``.
This package ``__init__`` does NOT import those modules, so importing any
stdlib-only submodule (``subject``, ``errors``, ``schema``) — which the core
(``engine.collision``) does — never pulls ruamel/frontmatter into the core.

The ruamel-backed adapter functions (``parse_file``/``write_file``/``hydrate``/
``serialize``) and ``RuamelRTHandler`` are therefore NOT re-exported here; non-core
consumers (the migrator, the CLI) import them directly::

    from seahorse.frontmatter.adapter import parse_file, write_file, hydrate, serialize
    from seahorse.frontmatter.handler import RuamelRTHandler

The symbols re-exported below are ruamel-free (stdlib + pydantic + contracts).
"""

from __future__ import annotations

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.errors import (
    FrontmatterInvalid,
    MigrationError,
    SubjectEmpty,
    XReservedCollision,
)
from seahorse.frontmatter.schema import (
    CognitiveType,
    Provenance,
    SupersedesReason,
    validate_for_write,
)
from seahorse.frontmatter.subject import derive_subject, fact_id_of

__all__ = [
    "CognitiveType",
    "Episode",
    "FrontmatterInvalid",
    "MigrationError",
    "Provenance",
    "SubjectEmpty",
    "SupersedesReason",
    "XReservedCollision",
    "derive_subject",
    "fact_id_of",
    "validate_for_write",
]