"""Local Ollama provider (spec §4, Phase 1 default).

Talks to an Ollama daemon on localhost (default http://localhost:11434)
using only the stdlib — no httpx dependency, which keeps Phase 1 at zero
runtime dependencies. Blocking urllib calls run in a thread via
``asyncio.to_thread`` so the scheduler loop is never stalled.

VRAM note (RTX 4050, 6GB): model *choice* is config, not code — the default
below fits in 6GB (qwen2.5-coder:7b at q4 ≈ 4.7GB). Larger models belong to
the NIM/OpenRouter providers, not local.

Testability: ``http_get``/``http_post`` are injectable so unit tests don't
need a live Ollama daemon.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .base import (
    Completion,
    Message,
    Provider,
    ProviderAuthError,
    ProviderHealth,
    ProviderUnavailable,
    RateLimited,
)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:7b"


def _real_http(method: str, url: str, payload: Optional[dict], timeout: float) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


HttpFn = Callable[[str, str, Optional[dict], float], dict]


class LocalOllamaProvider(Provider):
    name = "local_ollama"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        default_model: str = DEFAULT_MODEL,
        timeout_s: float = 120.0,
        http_fn: Optional[HttpFn] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout_s = timeout_s
        self._http = http_fn or _real_http

    async def complete(
        self,
        messages: list[Message | dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Completion:
        use_model = model or self.default_model
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload = {
            "model": use_model,
            "messages": self.normalize(messages),
            "stream": False,
            "options": options,
        }
        started = time.monotonic()
        body = await asyncio.to_thread(
            self._call, "POST", f"{self.base_url}/api/chat", payload, self.timeout_s
        )
        latency = time.monotonic() - started
        message = body.get("message") or {}
        return Completion(
            content=message.get("content", ""),
            model=body.get("model", use_model),
            provider=self.name,
            prompt_tokens=int(body.get("prompt_eval_count") or 0),
            completion_tokens=int(body.get("eval_count") or 0),
            latency_s=latency,
            raw=body,
        )

    async def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            body = await asyncio.to_thread(
                self._call, "GET", f"{self.base_url}/api/tags", None, 5.0
            )
        except ProviderUnavailable as exc:
            return ProviderHealth(False, detail=str(exc))
        models = [m.get("name", "") for m in body.get("models", [])]
        return ProviderHealth(
            True,
            latency_s=time.monotonic() - started,
            detail=f"{len(models)} model(s): {', '.join(models) or 'none pulled'}",
        )

    # -------------------------------------------------------------- internals

    def _call(self, method: str, url: str, payload: Optional[dict], timeout: float) -> dict:
        try:
            return self._http(method, url, payload, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                raise RateLimited(
                    f"ollama 429 for {url}",
                    float(retry_after) if retry_after else None,
                ) from exc
            if exc.code in (401, 403):
                raise ProviderAuthError(f"ollama {exc.code} for {url}") from exc
            raise ProviderUnavailable(f"ollama HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"ollama unreachable at {url}: {exc}") from exc
