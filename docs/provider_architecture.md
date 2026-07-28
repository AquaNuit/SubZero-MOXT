# Provider Architecture

## The contract (built, Phase 1)

`providers/base.py` — the entire surface a provider implements:

```python
class Provider(abc.ABC):
    name: str
    async def complete(self, messages, *, model=None, temperature=0.2,
                       max_tokens=None, **kw) -> Completion: ...
    async def health(self) -> ProviderHealth: ...
```

- `Completion`: content, model, provider, token counts, latency, raw body.
- `ProviderHealth`: available / latency / detail. Used by the router's
  health-based reweighting (Phase 4.5).

**Error contract** — providers translate their failures into the kernel's
marker exceptions so Recovery classifies without knowing provider internals:

| Condition | Raised | Kernel class |
|-----------|--------|--------------|
| Connection refused/timeout, HTTP 5xx | `ProviderUnavailable` | transient → retry |
| HTTP 429 | `RateLimited` (carries `retry_after`) | transient → retry |
| HTTP 401/403 | `ProviderAuthError` | environment → (Phase 4.5: needs_human) |

**Registry** — `providers/__init__.py`: `get_provider(name)` /
`registered_providers()`. Adding a provider = one new file implementing the
ABC + one registry line. No other module changes; the rest of the system
must not know or care which provider is in use.

## LocalOllamaProvider (built)

- stdlib `urllib` inside `asyncio.to_thread` — zero dependencies, loop
  never blocked.
- `http_fn` injectable → tests need no live daemon.
- Default model `qwen2.5-coder:7b` (~4.7GB at q4) — chosen to fit the 6GB
  VRAM budget alongside the OS; larger models belong to remote providers.

## Router (planned, Phase 4.5)

Reads `config/routing.yaml` (already written — keep routing as config,
never code):

- **Task classifier** tags each task `trivial | moderate | hard | critical`
  (cheap heuristic first, small local model later — upgradeable).
- **Routing table** maps class → ordered candidate providers + `verify` +
  `quorum`. `fallback_order` for global degradation.
- **Dynamic reweighting**: rolling success-rate + latency per provider
  quietly deprioritizes a degrading provider — adaptive, not just reactive
  to hard 429/5xx. Health data comes from `health()` + observed call
  outcomes; store the rolling stats in the kernel DB (new table, services
  may read but only the router writes).
- **Verifier/critic pass** (`verify: true`): a second, independent model
  call checks the first output against the stated goal (contradictions,
  hallucinated paths/APIs/facts, unverified claims) before the result is
  written to the task graph. `quorum: 2` on `critical`: two independent
  calls must agree; tie-break with a third.

## NIM key pool (planned, Phase 4.5)

`ProviderKeyPool`: round-robins across multiple legitimately-held NVIDIA
NIM keys; rolling per-key request window against the 40rpm limit; a 429
backs that key off (default 30s, see `nim_key_pool` in routing.yaml)
instead of failing the task. Pool-level failover acceptance test: "pull the
network cable on the main key mid-task → pool fails over".

## OpenRouter policy (planned, Phase 4.5)

Free tier (~5 calls/day) is reserved for `critical`-classified tasks only
(`free_tier_reserved_for: critical` in routing.yaml) — routine work must
never burn it.

## OpenCode Zen (planned, Phase 4.5)

Fourth provider; same ABC, registered as `opencode_zen`.
