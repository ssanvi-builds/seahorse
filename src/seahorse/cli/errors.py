"""CLI-owned errors — Cat C exit codes from the CLI surface itself.

These are NOT from the ``SeahorseError`` catalog of the facade (which the CLI
only translates, never invents). They are bootstrap / config / reserved-feature
failures of the CLI layer, prefixed ``CLI_`` to distinguish them from ``E_``
domain codes.

``CliError`` is the base: it carries its own ``exit_code`` (Cat C) and an
``info()`` payload for stderr. ``translate()`` in ``exit_codes`` short-circuits
on ``CliError`` so the CLI-owned code wins over any incidental domain shape.

Cat C codes (owned by the CLI):
- ``CLI_NOT_IN_MVP_0`` (75) — ``expire``/``revalidate`` refused at the CLI
  layer, plus management commands whose dependencies are not built in the
  current release (the frontmatter migrator, the canonical-format parser, the
  embedder). Fail-loud: the command exists on the surface and is reserved, it
  does not silently disappear.
- ``CLI_VAULT_NOT_FOUND`` (82) — vault resolution failed.
- ``CLI_CONFIG_INVALID`` (83) — ``seahorse.toml`` failed to parse/load.
- ``CLI_REBUILD_CONFLICTS`` (94) — ``seahorse index rebuild`` found conflicts
  (incompatible/malformed rows) and fails loud instead of auto-picking; the
  operator decides. Split off the shared 75 exit so the rebuild-conflict
  concern is distinct on the int, not only on the payload.
- ``CLI_OBSERVER_RUNNING`` (95) — ``seahorse observe start`` while the observer
  is already running (single-writer lock). Note: 95 is a shared exit — the
  engine's ``E_IMPROVE_VALID_AT_FUTURE`` (Cat A) also maps to 95; see the NOTE
  in ``exit_codes.py`` (distinguish via the structured payload, never the int).
- ``CLI_MIGRATION_DEFERRED`` (97) — ``seahorse frontmatter migrate`` ended with
  case-D notes deferred (the run completes but the vault is not fully
  migrated; scripts must see it).
- ``CLI_MATERIALIZE_NOT_CONFIGURED`` (98) — ``seahorse materialize`` on a vault
  without a ``[materialize]`` section (the feature is opt-in; fail-loud with
  the setup hint).
- ``CLI_HTTP_TOKEN_MISSING`` (99) — ``--transport http`` with no bearer token
  configured anywhere. Fail-loud BEFORE bind: the HTTP server must never start
  unauthenticated.
- ``CLI_CLOUDFLARED_MISSING`` (100) — ``seahorse remote`` with cloudflared
  absent (install offer declined or impossible: no TTY, no brew). Names the
  install line; the wizard refuses to half-start. First Cat C code ABOVE the
  closed 64–99 band (see ``exit_codes.py``).
- ``CLI_REMOTE_START_FAILED`` (101) — a remote child (HTTP server or tunnel)
  died before readiness. The wizard tears down what it spawned; the detail
  names the child and its log path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from seahorse.cli.exit_codes import (
    CLI_CLOUDFLARED_MISSING,
    CLI_CONFIG_INVALID,
    CLI_HTTP_TOKEN_MISSING,
    CLI_MATERIALIZE_NOT_CONFIGURED,
    CLI_MIGRATION_DEFERRED,
    CLI_NOT_IN_MVP_0,
    CLI_OBSERVER_RUNNING,
    CLI_REBUILD_CONFLICTS,
    CLI_REMOTE_START_FAILED,
    CLI_VAULT_NOT_FOUND,
    EXIT_USAGE,
)


class CliError(Exception):
    """Base for CLI-owned (Cat C) errors. Carries its own ``exit_code``."""

    __slots__ = ("exit_code", "detail", "name")

    def __init__(self, *, exit_code: int, name: str, detail: str) -> None:
        self.exit_code = exit_code
        self.name = name  # CLI_* symbolic name (stable for scripting)
        self.detail = detail
        super().__init__(f"{name}: {detail}")

    def info(self) -> dict[str, Any]:
        """Structured payload for stderr (human form / ``--json`` form)."""
        return {
            "cli_code": self.name,
            "detail": self.detail,
            "component": "#14",
            "exit_code": self.exit_code,
        }


class CliNotInMVP0(CliError):
    """A command reserved in the current release was invoked.

    ``expire`` / ``revalidate`` are intercepted at the CLI layer (never reaching
    ``facade.expire``/``revalidate``, which raise ``E_NOT_IN_MVP_0_1``). The
    same Cat C code covers management commands whose dependencies are not built
    in the current release (the frontmatter migrator, the canonical-format
    parser, the embedder) — fail-loud on the surface so the user sees the
    command is reserved, not absent.
    """

    def __init__(self, command: str, *, reason: str) -> None:
        super().__init__(
            exit_code=CLI_NOT_IN_MVP_0,
            name="CLI_NOT_IN_MVP_0",
            detail=f"`seahorse {command}` is reserved in the current release: {reason}",
        )
        self.command = command


class CliVaultNotFound(CliError):
    """Vault resolution failed (--vault / env / .seahorse/ all missed)."""

    def __init__(
        self,
        *,
        hint: str = "run `seahorse setup` (it bootstraps + registers a vault)",
    ) -> None:
        super().__init__(
            exit_code=CLI_VAULT_NOT_FOUND,
            name="CLI_VAULT_NOT_FOUND",
            detail=f"no Seahorse vault resolved; {hint}",
        )


class CliConfigInvalid(CliError):
    """``seahorse.toml`` failed to load/parse."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            exit_code=CLI_CONFIG_INVALID,
            name="CLI_CONFIG_INVALID",
            detail=f"seahorse.toml invalid: {detail}",
        )


class CliUsageError(CliError):
    """CLI-shape validation failure (exit 2, POSIX usage).

    Raised for: argument size caps exceeded (body/query/reason too long),
    vocabulary membership violations the facade does NOT enforce
    (``--source-type`` / ``--cognitive-type`` value not in the canonical set),
    and unparseable ISO-8601 timestamps. These are CLI-border guards — the
    facade's own ``SeahorseError`` checks (empty body, missing source_type,
    invalid extraction_mode, PIT) are left to surface as Cat A exit codes (64+),
    so the CLI does not invent ``SeahorseError`` codes (it only translates).
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            exit_code=EXIT_USAGE,
            name="CLI_USAGE",
            detail=detail,
        )


class CliObserverRunning(CliError):
    """``seahorse observe start`` when the observer is already running.

    The observer is a single-writer process; a second ``start`` would spawn a
    competing writer. Fail loud at exit 95 — the operator must ``seahorse
    observe stop`` first.
    """

    def __init__(self, pid: int | None = None) -> None:
        self.pid = pid
        detail = (
            f"observer is already running (pid {pid}); run `seahorse observe stop` first"
            if pid is not None
            else "observer is already running (single-writer lock held)"
        )
        super().__init__(
            exit_code=CLI_OBSERVER_RUNNING,
            name="CLI_OBSERVER_RUNNING",
            detail=detail,
        )


class CliMigrationDeferred(CliError):
    """``seahorse frontmatter migrate`` met incompatible notes — fail-loud.

    The migrator classifies every note (A/B/C/D): A/B are migrated, C is
    idempotent, D is REFUSED (logged, never overwritten). When apply meets one
    or more case-D notes the run completes but the vault is not fully migrated,
    so the CLI fails loud at exit 97 (Cat C) — a script must see that manual
    resolution is required before ``index rebuild`` can succeed on those notes.
    The manifest summary is rendered to stdout FIRST (index-rebuild pattern) so
    the operator sees the deferred list AND the error.

    ``count`` is the number of case-D notes; the structured payload carries it
    so ``--json`` consumers can detect the partial state without re-running.
    """

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            exit_code=CLI_MIGRATION_DEFERRED,
            name="CLI_MIGRATION_DEFERRED",
            detail=(
                f"frontmatter migrate stopped: {count} incompatible note(s) "
                "(case D) require manual resolution"
            ),
        )

    def info(self) -> dict[str, Any]:
        payload = super().info()
        payload["deferred"] = self.count
        return payload


class CliRebuildConflicts(CliError):
    """``seahorse index rebuild`` found conflicts — fail-loud honesty.

    The rebuild pre-pass detects conflicting facts (duplicate current-state
    ``fact_id`` or duplicate ``ep_id``) and refuses to auto-pick a winner.
    Instead it reports the conflict list (via ``info()["conflicts"]``) and fails
    loud at exit 94 so a human decides. No silent no-op, no silent drop.

    ``count`` is the number of skipped/conflicting facts; the structured payload
    carries the human-readable conflict summary so ``--json`` consumers can list
    them without re-running the rebuild.
    """

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            exit_code=CLI_REBUILD_CONFLICTS,
            name="CLI_REBUILD_CONFLICTS",
            detail=f"index rebuild stopped: {count} conflicting fact(s) require human resolution",
        )

    def info(self) -> dict[str, Any]:
        payload = super().info()
        payload["conflicts"] = self.count
        return payload


class CliMaterializeNotConfigured(CliError):
    """``seahorse materialize`` when the vault has no ``[materialize]`` section.

    Materialization is opt-in (the section is absent by default). The backfill
    needs the section's ``dir``/``mode`` to know where and what to write, so a
    missing section fails loud at exit 98 (Cat C) with the setup hint — the
    operator runs ``seahorse setup`` (which writes the section) or adds it by
    hand.
    """

    def __init__(self) -> None:
        super().__init__(
            exit_code=CLI_MATERIALIZE_NOT_CONFIGURED,
            name="CLI_MATERIALIZE_NOT_CONFIGURED",
            detail=(
                "materialization is not configured for this vault; run "
                "`seahorse setup` or add a [materialize] section to seahorse.toml"
            ),
        )


class CliHttpTokenMissing(CliError):
    """``--transport http`` with no bearer token configured anywhere — fail
    loud, no bind.

    The token is the only barrier between the 15 tools and the network, so the
    HTTP server MUST never start without one: this fails loud at exit 99 (Cat
    C) before any socket exists. The caller checks all three sources (flag/env
    /``[http]``) first; this error only names the actionable fix.
    """

    def __init__(self) -> None:
        super().__init__(
            exit_code=CLI_HTTP_TOKEN_MISSING,
            name="CLI_HTTP_TOKEN_MISSING",
            detail=(
                "no HTTP bearer token found; set SEAHORSE_HTTP_TOKEN or add a "
                "[http] token to seahorse.toml (`seahorse setup` writes one)"
            ),
        )


class CliCloudflaredMissing(CliError):
    """``seahorse remote`` needs cloudflared and it is not installed.

    Raised after the install offer is declined or impossible (no TTY, no
    brew): the wizard refuses to half-start — no server is spawned behind a
    tunnel that cannot exist. The detail carries the install line.
    """

    def __init__(self, install_line: str) -> None:
        super().__init__(
            exit_code=CLI_CLOUDFLARED_MISSING,
            name="CLI_CLOUDFLARED_MISSING",
            detail=f"cloudflared not found; install it first — {install_line}",
        )


class CliRemoteStartFailed(CliError):
    """A ``seahorse remote`` child (server or tunnel) died before readiness.

    The wizard tears down whatever it spawned before raising — no
    half-started state survives this exit. The detail names the child and
    its log so the operator can diagnose without re-running.
    """

    def __init__(self, child: str, log_path: Path) -> None:
        super().__init__(
            exit_code=CLI_REMOTE_START_FAILED,
            name="CLI_REMOTE_START_FAILED",
            detail=f"{child} exited before becoming ready; log: {log_path}",
        )


__all__ = [
    "CliError",
    "CliNotInMVP0",
    "CliVaultNotFound",
    "CliConfigInvalid",
    "CliUsageError",
    "CliMigrationDeferred",
    "CliRebuildConflicts",
    "CliMaterializeNotConfigured",
    "CliHttpTokenMissing",
    "CliCloudflaredMissing",
    "CliRemoteStartFailed",
]