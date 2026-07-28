"""Recovery Manager — kernel-owned (spec §2.3).

Phase 1 ships the structure and the transient-failure policy; the full
classification matrix (logic -> replan, environment -> needs_human) lands
with the Recovery Manager upgrade in Phase 4.5, once the planner exists to
replan with.

Failure classification contract: services (providers, tools, plugins) raise
the marker exceptions defined here so the kernel can classify without
knowing service internals. This is part of the kernel's public interface —
providers/base.py subclasses them, and the Plugin SDK will too.

Escalation policy (never spin silently past caps):
    transient -> retry, capped, exponential backoff -> terminal failed
    logic / environment (Phase 1) -> terminal failed with the reason named;
                                     replan + needs_human escalation arrive
                                     in Phase 4.5.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ------------------------------------------------------------- marker errors
# Services raise these (or subclasses) so Recovery can classify the failure
# without importing service code.

class TransientError(Exception):
    """Retryable as-is: network blip, provider 429/5xx, temporary lock."""


class LogicError(Exception):
    """The plan/approach was wrong. Retrying the same steps won't help."""


class EnvironmentFailure(Exception):
    """Missing dependency, permission denied, disk full, bad credentials."""


class HardGatePending(Exception):
    """Not a failure: the task is waiting on a human approval (spec §1)."""


class FailureClass(enum.Enum):
    TRANSIENT = "transient"
    LOGIC = "logic"
    ENVIRONMENT = "environment"
    HARD_GATE_PENDING = "hard_gate_pending"


class RecoveryAction(enum.Enum):
    RETRY = "retry"
    REPLAN = "replan"              # Phase 4.5
    NEEDS_HUMAN = "needs_human"    # Phase 4.5 escalation
    FAILED = "failed"              # terminal


@dataclass
class RecoveryDecision:
    action: RecoveryAction
    failure_class: FailureClass
    reason: str
    delay_s: float = 0.0


class RecoveryManager:
    """Classifies failures and decides what the scheduler does next."""

    def __init__(self, *, max_retries: int = 3, base_backoff_s: float = 1.0):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.max_retries = max_retries
        self.base_backoff_s = base_backoff_s

    def classify(self, exc: BaseException) -> FailureClass:
        if isinstance(exc, HardGatePending):
            return FailureClass.HARD_GATE_PENDING
        if isinstance(exc, TransientError):
            return FailureClass.TRANSIENT
        if isinstance(exc, LogicError):
            return FailureClass.LOGIC
        if isinstance(exc, EnvironmentFailure):
            return FailureClass.ENVIRONMENT
        # Unknown service exceptions are treated as transient: one retry
        # cycle is cheap, and misclassifying a blip as terminal is worse.
        return FailureClass.TRANSIENT

    def backoff(self, retry_count: int) -> float:
        """Exponential backoff: base * 2^n, capped at 60s."""
        return min(self.base_backoff_s * (2 ** max(retry_count, 0)), 60.0)

    def handle(self, task, exc: BaseException) -> RecoveryDecision:
        """Decide the next step for a failed task. No side effects — the
        scheduler applies the decision so all graph/event writes stay in one
        place."""
        failure_class = self.classify(exc)
        reason = f"{type(exc).__name__}: {exc}"

        if failure_class is FailureClass.HARD_GATE_PENDING:
            # Should not normally reach the failure path at all — the gate
            # blocks inside the worker. Defensive: never retry, never fail.
            return RecoveryDecision(
                RecoveryAction.NEEDS_HUMAN, failure_class,
                "task is waiting on a human approval",
            )

        if failure_class is FailureClass.TRANSIENT and task.retry_count < self.max_retries:
            return RecoveryDecision(
                RecoveryAction.RETRY, failure_class, reason,
                delay_s=self.backoff(task.retry_count),
            )

        # Phase 1 terminal cases. Phase 4.5 turns LOGIC into capped replans
        # and ENVIRONMENT/exhausted retries into a needs_human escalation
        # naming the specific blocker.
        if failure_class is FailureClass.TRANSIENT:
            reason = f"transient retries exhausted ({self.max_retries}): {reason}"
        elif failure_class is FailureClass.LOGIC:
            reason = f"logic failure (replan lands in Phase 4.5): {reason}"
        else:
            reason = f"environment failure (needs human or different tool): {reason}"
        return RecoveryDecision(RecoveryAction.FAILED, failure_class, reason)
