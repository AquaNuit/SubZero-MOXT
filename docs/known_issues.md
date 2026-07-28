# Known Issues

Honest list, newest first. Each entry: what, why it's acceptable for now,
when it gets fixed.

## Open

0.i **The coding agent has only run against scripted providers.** All
   plumbing is real (indexer, edits, test runs, scheduler) but prompt
   quality for a REAL small local model (qwen2.5-coder:7b) is unverified —
   expect plan-parse retries to be frequent at first. *Fix: smoke-run on
   AT's laptop against Ollama; tune Planner.SYSTEM_PROMPT; consider
   grammar/JSON-mode decoding (Ollama `format: json`) in Phase 4.5.*

0.ii **Edit granularity is whole-block find/replace.** Large files /
   repeated blocks force `find`-disambiguation replans (burns iterations).
   Acceptable for the Phase 4 loop; *fix: line-anchored or AST-scoped
   edits if real-world usage shows iteration waste.*

0.a **No audit log of tool calls yet** (spec §8). The executor is the
   single call path, so this is one hook away; it lands with the
   permission-modes layer. *Fix: Phase 4.5.*

0.b **Dry-run exists only for `package_manager`.** Spec §8 wants dry-run
   "where feasible"; git/filesystem/shell dry-runs are not meaningful the
   same way, but docker `run` could preview. *Fix: as needed in Phase 4.5.*

0.c **`sudo` is assumed in package commands** (`sudo apt-get ...`). On a
   passwordless-sudo laptop this is fine; elsewhere installs will fail as
   `environment` (correct classification, human gets a named blocker in
   Phase 4.5). *Fix: optional privilege-escalation strategy config later.*

0.d **Docker tool is tested hermetically only** (sandbox has no daemon).
   Command construction + failure paths are asserted; the real-daemon path
   needs a smoke test on AT's laptop. *Fix: first real run; keep an eye on
   `docker info` probe latency.*

0. **`LLMSummarizer` is synchronous** (`asyncio.run` inside `summarize`).
   Called from an async worker it hits "event loop already running" and
   falls back to the heuristic summarizer — safe, but the LLM path is
   effectively sync-context only. *Fix: Phase 4, add `async_summarize`
   when the real planner worker lands.*

0.1 **`HashEmbedder` is keyword-grade, not semantic.** Retrieval quality
   depends on vocabulary overlap; paraphrases won't match. Fine for
   plumbing tests + small workspaces; *fix: drop in a real embedding model
   (Ollama `nomic-embed-text` class, <1GB VRAM) behind the existing
   `Embedder` protocol when the agent runs on AT's laptop.*

0.2 **`HeuristicTokenCounter` (chars/4) is an estimate.** Pressure may fire
   early/late vs. a real tokenizer. The failure mode it prevents (hard
   over-length API rejection) is worse than a premature summary. *Fix:
   plug a real tokenizer behind `TokenCounter` with the Phase 4 router.*

0.3 **Dependency-graph module resolution is heuristic** (dotted path +
   stem matching; no aliasing/`from x import y as z` awareness). It's a
   graph hint — the LLM confirms before editing. *Fix: tree-sitter upgrade
   when justified (new dep).*

1. **Logic/environment failures terminate instead of escalating.**
   `RecoveryManager` classifies them correctly but marks the task `failed`
   (Phase 1 policy). Replan-on-logic needs the Phase 4 planner;
   needs_human-on-environment needs the Phase 4.5 notifier. *Fix: Phase 4.5
   (structure and `RecoveryAction` values already exist).*

2. **Retry backoff sleeps occupy a dispatch slot.**
   `_handle_failure` awaits the backoff inside the dispatch coroutine, so a
   task in backoff holds one of the `max_concurrency=4` slots. Acceptable
   at this scale (worst case: 4 concurrent 60s backoffs stall dispatch);
   *fix: Phase 4.5, move to a `not_before` timestamp on the task row and
   re-queue without holding a slot.*

3. **Dead-letter alerts are log-only.**
   Poison events land durably in `dead_letters` + `logger.error`, but
   nobody is paged. *Fix: Phase 4.5, a notifier consumer surfaces them
   (Telegram/Discord).*

4. **Single-process scheduler.**
   No distributed workers; subagent processes arrive with Phase 6
   orchestration. *By design for a single laptop.*

5. **No planner/LLM worker yet.**
   The scheduler dispatches a `worker` callable, but the LLM-driven
   plan/execute worker is Phase 4. The provider interface, routing config,
   and (as of Phase 2) the memory system it will consume are ready.
   *Fix: Phase 4.*

6. **`config/routing.yaml` is unread.**
   Written per spec §4 so routing stays config; nothing parses it until the
   Phase 4.5 router. *Fix: Phase 4.5 (add PyYAML dep then, justified in
   changelog).*

7. **`needs_human` tasks re-dispatch immediately after a crash even with no
   approval recorded**, and the re-dispatched worker re-enters the gate and
   waits again. Correct but worth knowing: an approval given *during* the
   dead window is never lost (approvals table is the source of truth).
   *Behavior verified in `tests/test_hard_gate.py`; revisit if Phase 4.5
   shows UX friction.*

## Resolved

_None yet._
