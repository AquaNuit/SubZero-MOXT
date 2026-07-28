"""SubZero kernel: the small, trusted core (spec §2).

Owns: task graph persistence, scheduler loop, event bus, recovery policy,
and the hard-gate enforcement point. Everything else (providers, tools,
memory, notifiers, plugins) is a service the kernel dispatches to.
"""

from .task_graph import TaskGraph, TaskNode
from .event_bus import EventBus, Event
from .scheduler import Scheduler, WorkerResult, SubtaskSpec, WorkerContext
from .recovery import (
    RecoveryManager,
    FailureClass,
    TransientError,
    LogicError,
    EnvironmentFailure,
    HardGatePending,
)
from .hard_gate_enforcer import HardGateEnforcer, HardGateDenied, ToolCallSpec

__all__ = [
    "TaskGraph",
    "TaskNode",
    "EventBus",
    "Event",
    "Scheduler",
    "WorkerResult",
    "SubtaskSpec",
    "WorkerContext",
    "RecoveryManager",
    "FailureClass",
    "TransientError",
    "LogicError",
    "EnvironmentFailure",
    "HardGatePending",
    "HardGateEnforcer",
    "HardGateDenied",
    "ToolCallSpec",
]
