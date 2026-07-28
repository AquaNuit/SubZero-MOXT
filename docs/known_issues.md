# Known Issues

Honest list, newest first. Each entry: what, why it's acceptable for now,
when it gets fixed.

## Open

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
   plan/execute worker is Phase 4. The provider interface and routing
   config are ready for it. *Fix: Phase 4.*

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
