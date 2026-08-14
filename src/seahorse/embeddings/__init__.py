"""Embeddings interface.

Core types (``ModelIdentity``, the async ``Embedder`` Protocol) live in
``seahorse.embeddings.types`` and are importable WITHOUT numpy at top level
(numpy is lazy inside ``_l2_normalize``); the FastEmbed ONNX backend and the
sync ``QueryEmbedder`` adapter are provided alongside.
"""

from seahorse.embeddings.types import Embedder, ModelIdentity, Role

__all__ = ["Embedder", "ModelIdentity", "Role"]
