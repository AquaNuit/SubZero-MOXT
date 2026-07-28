# Performance

## The budget (spec preamble)

Target hardware: Linux laptop, RTX 4050 (**6GB VRAM**), **16GB RAM**,
quad-core+ CPU. Every design decision is checked against this before being
accepted.

## Phase 1 observations

- **Zero runtime dependencies.** Nothing to install, nothing resident but
  the agent process itself.
- **One SQLite file** (WAL) holds all kernel state. Measured footprint of
  the full 35-test suite: completes in ~3.7s; per-test DBs are throwaway
  tmp files. WAL adds `-wal`/`-shm` sidecars, checkpointed on close —
  negligible at this scale.
- **One asyncio process**, `max_concurrency=4` worker slots. Idle loop cost
  is one `asyncio.sleep(poll_interval)` — effectively zero CPU.
- **No VRAM usage yet** — the only provider is an interface + a client.
  When Ollama runs: default model `qwen2.5-coder:7b` q4 ≈ 4.7GB, leaving
  ~1.3GB headroom under the 6GB ceiling. Anything larger routes to remote
  providers (NIM/OpenRouter) — that's a routing-table concern
  (`config/routing.yaml`), not a code branch.
- Retry backoff currently sleeps inside a dispatch slot (max 60s cap). At
  `max_concurrency=4` a saturated backoff wave could reduce throughput —
  noted in `known_issues.md`, fix lands with the Phase 4.5 recovery upgrade.

## Standing rules

1. No always-on services besides the agent process and (optionally) Ollama.
   No Redis, no vector-DB server, no message broker.
2. Local models must state their q-level VRAM footprint before being made
   a default; the total (model + OS + browser when Phase 5 lands) must stay
   under 6GB VRAM / 16GB RAM with headroom.
3. Measure before optimizing: entries go in `optimization_log.md` with
   before/after numbers, not vibes.

## Phase 7 measurement plan (acceptance: steady-state under budget)

- Long-running soak task (multi-hour) with resident-set-size sampling of
  the agent process + Ollama; assert RAM plateau, no leak-driven growth.
- VRAM sampling (`nvidia-smi --query-gpu=memory.used`) across a coding
  workload; assert steady-state ≤ budget with headroom.
- SQLite file growth over the soak; define a checkpoint/vacuum policy for
  the events table if growth is material.
- Cold-start resume time (process start → first task re-dispatched).
