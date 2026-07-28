"""Provider interface (spec §4).

Single method surface: ``complete(messages, **kwargs) -> Completion`` plus a
``health()`` check. Adding a fifth provider later = one new file implementing
this ABC; the rest of the system must not know or care which provider is in
use. The router (Phase 4.5) selects among providers via config/routing.yaml.

Error contract: providers raise the kernel's marker exceptions so Recovery
can classify without importing provider code:
- ProviderUnavailable / RateLimited  -> TransientError  (retry with backoff)
- ProviderAuthError                  -> EnvironmentFailure (bad/missing key)
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from kernel.recovery import EnvironmentFailure, TransientError


class ProviderError(TransientError):
    """Base for retryable provider failures."""


class ProviderUnavailable(ProviderError):
    """Connection refused, timeout, 5xx — retryable."""


class RateLimited(ProviderError):
    """HTTP 429. ``retry_after`` (seconds) hints the backoff, if given."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderAuthError(EnvironmentFailure):
    """401/403 — missing or bad credentials. Retrying won't help."""


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Completion:
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    raw: Optional[dict[str, Any]] = field(default=None, repr=False)


@dataclass
class ProviderHealth:
    available: bool
    latency_s: Optional[float] = None
    detail: str = ""


class Provider(abc.ABC):
    """The whole interface. Implement this, register it, done."""

    name: str = "abstract"

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[Message | dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Completion:
        ...

    @abc.abstractmethod
    async def health(self) -> ProviderHealth:
        ...

    # ------------------------------------------------------------ helpers

    @staticmethod
    def normalize(messages: list[Message | dict[str, str]]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for m in messages:
            out.append(m.as_dict() if isinstance(m, Message) else dict(m))
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r}>"
