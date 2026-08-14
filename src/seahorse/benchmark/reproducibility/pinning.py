"""Model pinning — tag + digest identity.

``pin_ollama_model`` reuses the LLM backend's digest-resolution mechanism
(``ollama show``) to pin a model by its SHA-256 digest — the manifest records
``provider/model-tag@sha256:<digest>``, never a bare tag.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPin:
    """Pinned model identity: tag + digest + provider."""

    provider: str  # "ollama" | "openai" | "anthropic" | ...
    model_tag: str  # "qwen3:1.7b"
    digest_sha256: str  # "<64-hex>" from `ollama show --digest`

    @property
    def canonical(self) -> str:
        return f"{self.provider}/{self.model_tag}@sha256:{self.digest_sha256}"


def pin_ollama_model(model_tag: str) -> ModelPin:
    """Resolve a model tag to a pinned ``ModelPin`` via ``ollama show``.

    Reuses the LLM backend's digest-resolution mechanism (the ollama CLI) — not
    the LLM completion path. Raises if the tag is not pulled locally (an
    explicit pre-pull is required, fail-loud honesty).
    """
    out = subprocess.check_output(["ollama", "show", model_tag], text=True)
    m = re.search(r"sha256:([0-9a-f]{64})", out)
    if not m:
        raise RuntimeError(
            f"Could not resolve digest for ollama model {model_tag!r}; is it pulled?"
        )
    return ModelPin(provider="ollama", model_tag=model_tag, digest_sha256=m.group(1))


__all__ = ["ModelPin", "pin_ollama_model"]
