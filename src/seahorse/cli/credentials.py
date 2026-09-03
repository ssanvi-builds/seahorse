"""Local API-key store for providers configured via ``seahorse setup``.

A tiny JSON map of env-var NAME → key value (``{"GEMINI_API_KEY": "..."}``),
written 0600 and atomically (same-directory tempfile + ``os.replace``). The
key itself never lands in ``seahorse.toml`` (``LlmConfig`` has no key fields
by design) and never in a log line — every detail string carries env-var
names and paths only, and ``mask_secret`` is applied to any output that
could echo a key (e.g. a provider error message).

Every read path degrades to "absent" instead of raising: a missing or
hand-corrupted file must never brick a headless command. ``load_credentials_env``
sets only env-var names that are not already present in the environment —
an explicitly exported key always wins.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import MutableMapping
from pathlib import Path

from seahorse.cli.config import global_config_dir

CREDENTIALS_FILENAME = "credentials.json"
_PRIVATE_MODE = 0o600


def credentials_path() -> Path:
    """Path of the credentials store (``SEAHORSE_CREDENTIALS`` overrides)."""
    override = os.environ.get("SEAHORSE_CREDENTIALS")
    if override:
        return Path(override)
    return global_config_dir() / CREDENTIALS_FILENAME


def _read_store(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def _atomic_write_private(path: Path, data: dict[str, str]) -> None:
    """Write the store atomically with 0600, tightening a pre-existing loose file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, _PRIVATE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
        with contextlib.suppress(OSError):
            os.chmod(path, _PRIVATE_MODE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def save_api_key(api_key_env: str, key: str, *, path: Path | None = None) -> tuple[bool, str]:
    """Merge one key into the store. Never logs the value."""
    target = path or credentials_path()
    store = _read_store(target)
    store[api_key_env] = key
    try:
        _atomic_write_private(target, store)
    except OSError as exc:
        return False, f"cannot write {target}: {exc.strerror or exc}"
    return True, f"{api_key_env} stored in {target}"


def load_credentials_env(
    *,
    path: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Push stored keys into the environment (missing names only).

    Returns the env-var NAMES that were set. Never raises and never
    overrides an already-exported variable.
    """
    env = os.environ if environ is None else environ
    store = _read_store(path or credentials_path())
    set_names: list[str] = []
    for name, value in store.items():
        if name not in env:
            env[name] = value
            set_names.append(name)
    return set_names


def read_api_key(api_key_env: str, *, path: Path | None = None) -> str | None:
    """Read one stored key, or None when absent."""
    return _read_store(path or credentials_path()).get(api_key_env)


def check_permissions(*, path: Path | None = None) -> tuple[bool, str]:
    """(ok, detail) — ok when the store is 0600-or-stricter or absent."""
    target = path or credentials_path()
    if not target.exists():
        return True, f"no credentials file at {target}"
    mode = target.stat().st_mode & 0o777
    if mode & 0o077:
        return False, f"{target} permissions too open ({mode:04o}) — expected 0600"
    return True, f"{target} is 0600"


def mask_secret(text: str, *secrets: str) -> str:
    """Redact every non-empty secret occurrence from ``text``."""
    masked = text
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, "***")
    return masked


__all__ = [
    "CREDENTIALS_FILENAME",
    "check_permissions",
    "credentials_path",
    "load_credentials_env",
    "mask_secret",
    "read_api_key",
    "save_api_key",
]