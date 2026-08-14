"""``AdapterRegistry`` — pluggable dataset adapters.

Adding a benchmark = one ``adapter_xxx.py`` file with a ``@register``
decorator. The runner, metrics, and reporters never change.
"""

from __future__ import annotations

from seahorse.benchmark.contracts import DatasetLoader


class AdapterRegistry:
    """Registry of ``DatasetLoader`` classes, keyed by adapter name."""

    _adapters: dict[str, type[DatasetLoader]] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(loader_cls: type[DatasetLoader]) -> type[DatasetLoader]:
            cls._adapters[name] = loader_cls
            return loader_cls

        return decorator

    @classmethod
    def get(cls, name: str) -> type[DatasetLoader]:
        if name not in cls._adapters:
            raise KeyError(
                f"Unknown benchmark adapter: {name}. Available: {list(cls._adapters)}"
            )
        return cls._adapters[name]

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._adapters)


__all__ = ["AdapterRegistry"]
