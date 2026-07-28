"""Provider registry.

The router (Phase 4.5) resolves providers by name through here. Adding a
provider = implement ``providers.base.Provider`` in a new file and register
it below — no other module changes.
"""

from __future__ import annotations

from .base import (
    Completion,
    Message,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
    RateLimited,
)
from .local_ollama import LocalOllamaProvider

_REGISTRY = {
    LocalOllamaProvider.name: LocalOllamaProvider,
    # Phase 4.5: "nim_pool", "openrouter", "opencode_zen"
}


def get_provider(name: str, **kwargs) -> Provider:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown provider {name!r}; registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](**kwargs)


def registered_providers() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "Completion",
    "Message",
    "Provider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderHealth",
    "ProviderUnavailable",
    "RateLimited",
    "LocalOllamaProvider",
    "get_provider",
    "registered_providers",
]
