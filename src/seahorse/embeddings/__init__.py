"""#7 — Embeddings interface (f5-07).

Core types (``ModelIdentity``, the async ``Embedder`` Protocol) live in
``seahorse.embeddings.types`` and are importable WITHOUT numpy at top level
(numpy is lazy inside ``_l2_normalize``); the FastEmbed ONNX backend and the
sync ``QueryEmbedder`` adapter land in M1-B.2 / M1-B.3.
"""

from seahorse.embeddings.types import Embedder, ModelIdentity, Role

__all__ = ["Embedder", "ModelIdentity", "Role"]
