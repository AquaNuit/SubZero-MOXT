"""Coding worker (Phase 4): the LLM-driven plan/edit/test/debug loop.

This is the Scheduler `worker` implementation for coding tasks. The loop:

1. **Indexer first** (spec §5.1 / Phase 4 acceptance): scan the workspace,
   resolve identifier-like tokens from the goal via `where_is` — the
   structural "where does X live" question is answered deterministically,
   BEFORE any provider call, and the locations go into the prompt. No
   full-repo scan ever reaches the context.
2. **Assemble** minimal context (WorkingMemory rule: goal + parent summary
   + retrieval + indexer locations — nothing else).
3. **Plan** via the provider (Planner → structured edits + test command).
4. **Edit/test/debug loop**, bounded: apply exact find/replace edits via
   the ToolExecutor (filesystem), run the test command (shell), feed
   failures back into a replan with failure context (spec §2.3 — a
   genuinely different fix, not the same prompt twice).
5. **Close** (spec §5): re-index, compress the transcript to a structured
   summary, externalize the full transcript (`full_log_ref`), record the
   decision, return `WorkerResult`.

Everything goes through the ToolExecutor — the worker never touches tools
directly, so the hard-gate/audit story stays exactly one call path wide.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from kernel.recovery import LogicError, TransientError
from kernel.scheduler import WorkerResult
from memory.compression import HeuristicSummarizer, TranscriptStore
from memory.working_memory import ContextPressureMonitor, WorkingMemory
from .planner import Plan, Planner

log = logging.getLogger("agent.coding_worker")

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*(?:\(\))?")


@dataclass
class CodingWorkerConfig:
    max_iterations: int = 5
    test_timeout_s: float = 180.0
    window_tokens: int = 8192
    pressure_threshold: float = 0.70


class CodingWorker:
    """Scheduler-compatible: `await worker(task, ctx) -> WorkerResult`."""

    def __init__(
        self,
        provider,
        executor,
        *,
        workspace_root: str,
        indexer=None,
        retriever=None,
        decision_memory=None,
        transcript_dir: str = "state/transcripts",
        model: Optional[str] = None,
        config: Optional[CodingWorkerConfig] = None,
        trace: Optional[list] = None,
    ):
        self.planner = Planner(provider, model=model)
        self.executor = executor
        self.workspace_root = workspace_root
        self.indexer = indexer
        self.retriever = retriever
        self.decisions = decision_memory
        self.transcripts = TranscriptStore(transcript_dir)
        self.config = config or CodingWorkerConfig()
        self.monitor = ContextPressureMonitor(
            self.config.window_tokens,
            threshold=self.config.pressure_threshold, keep_last_n=6)
        self.summarizer = HeuristicSummarizer()
        self.wm = WorkingMemory(retriever)
        # Optional observability hook: (event, detail) tuples for tests.
        self.trace = trace

    # ------------------------------------------------------------------ main

    async def __call__(self, task, ctx) -> WorkerResult:
        transcript: list[dict[str, str]] = []

        # 1-2. Index + assemble (before ANY provider call).
        locations = self._index_first(task.goal)
        parent_summary = self._parent_summary(ctx, task.parent_id)
        messages = self.wm.assemble(
            goal=task.goal, parent_summary=parent_summary,
            retrieval_query=task.goal if self.retriever else None)
        if locations:
            messages.append({"role": "system", "content":
                             "Workspace index locations:\n" + locations})
        transcript.extend(messages)
        ctx.emit_progress("context assembled (indexer-first)")

        # 3-4. Plan, then bounded edit/test/debug loop.
        iterations = 0
        plan = await self._plan(messages, ctx)
        failure_report = ""
        while True:
            iterations += 1
            ctx.emit_progress(
                f"iteration {iterations}: applying {len(plan.edits)} "
                f"edit(s)")

            edit_error = await self._apply_edits(plan, transcript)
            if edit_error is None:
                test_ok, test_output = await self._run_tests(
                    plan.test_command, ctx, transcript)
            else:
                test_ok, test_output = False, edit_error

            if test_ok:
                break
            failure_report = test_output
            if iterations >= self.config.max_iterations:
                raise LogicError(
                    f"edit/test/debug loop exhausted "
                    f"{self.config.max_iterations} iterations; last "
                    f"failure: {failure_report[:300]}")
            ctx.emit_progress(
                f"iteration {iterations} failed; replanning with failure "
                "context")
            messages.append({
                "role": "user",
                "content": f"Iteration {iterations} failed:\n{test_output}"})
            messages, _, _ = self.monitor.compress_if_needed(
                messages, self.summarizer)
            transcript.extend(messages[-2:])
            plan = await self._replan(messages, failure_report, ctx)

        # 5. Close out.
        return await self._close(task, ctx, plan, iterations, transcript)

    # -------------------------------------------------------------- stages

    def _index_first(self, goal: str) -> str:
        """Indexer queries happen here — before any provider call."""
        if self.indexer is None:
            return ""
        self._trace("indexer.scan", self.workspace_root)
        self.indexer.scan()
        lines: list[str] = []
        for ident in _identifiers(goal):
            hits = self.indexer.where_is(ident)
            self._trace("indexer.where_is", f"{ident} -> {len(hits)}")
            for hit in hits[:3]:
                lines.append(
                    f"{hit.name} ({hit.kind}) at {hit.file_path}:{hit.line}"
                    f"  {hit.signature}")
        return "\n".join(lines)

    async def _plan(self, messages, ctx) -> Plan:
        self._trace("provider.call", "plan")
        ctx.emit_progress("planning via provider")
        return await self.planner.plan(messages)

    async def _replan(self, messages, failure_report, ctx) -> Plan:
        self._trace("provider.call", "replan")
        return await self.planner.replan_with_failure(messages,
                                                      failure_report)

    async def _apply_edits(self, plan: Plan, transcript) -> Optional[str]:
        """Apply each edit via the executor. Returns None on success, else
        the error text to feed back into the replan."""
        for edit in plan.edits:
            path = self._resolve(edit.path)
            read = await self.executor.execute(
                "filesystem", action="read", path=path)
            self._trace("tool", f"filesystem.read {edit.path}")
            if not read.ok:
                return f"cannot read {edit.path}: {read.error}"
            new_content, err = _apply_edit(read.output, edit)
            if err:
                return f"edit to {edit.path} failed: {err}"
            written = await self.executor.execute(
                "filesystem", action="write", path=path, content=new_content)
            self._trace("tool", f"filesystem.write {edit.path}")
            if not written.ok:
                return f"cannot write {edit.path}: {written.error}"
            transcript.append({
                "role": "assistant",
                "content": f"Edited {edit.path}: replaced "
                           f"{len(edit.find)} chars with "
                           f"{len(edit.replace)} chars."})
        return None

    async def _run_tests(self, test_command: str, ctx,
                         transcript) -> tuple[bool, str]:
        self._trace("tool", f"shell {test_command}")
        result = await self.executor.execute(
            "shell", command=test_command, cwd=self.workspace_root,
            timeout_s=self.config.test_timeout_s)
        transcript.append({
            "role": "tool",
            "content": f"$ {test_command}\n{result.output[-2000:]}"})
        self._trace("tests", "pass" if result.ok else "fail")
        return result.ok, result.output or result.error

    async def _close(self, task, ctx, plan: Plan,
                     iterations: int, transcript) -> WorkerResult:
        if self.indexer is not None:
            self.indexer.scan()  # keep the index truthful after edits
            self._trace("indexer.scan", "post-edit")
        summary = self.summarizer.summarize(transcript)
        ref = self.transcripts.save(task.id, transcript)
        if self.decisions is not None:
            self.decisions.record(
                f"fixed '{task.goal[:80]}' via {len(plan.edits)} edit(s) "
                f"in {iterations} iteration(s)",
                task_id=task.id, outcome="tests pass")
            self._trace("decision.recorded", task.id)
        result_summary = (f"Fixed in {iterations} iteration(s). "
                          + summary.as_result_summary())
        ctx.emit_progress("task closed with structured summary")
        return WorkerResult(
            result_summary=result_summary,
            artifacts=[e.path for e in plan.edits],
            full_log_ref=ref)

    # -------------------------------------------------------------- helpers

    def _resolve(self, path: str) -> str:
        import os
        if os.path.isabs(path):
            return path
        return os.path.join(self.workspace_root, path)

    def _parent_summary(self, ctx, parent_id: Optional[str]) -> Optional[str]:
        if not parent_id:
            return None
        parent = ctx.graph.get(parent_id)
        return parent.result_summary if parent else None

    def _trace(self, event: str, detail: Any) -> None:
        if self.trace is not None:
            self.trace.append((event, str(detail)))


def _identifiers(goal: str) -> list[str]:
    """Identifier-like tokens from the goal, dotted parts split, ()
    stripped, deduped — candidates for `where_is` lookups."""
    out: list[str] = []
    for token in _IDENT_RE.findall(goal):
        token = token.rstrip("()")
        for part in token.split("."):
            if len(part) >= 3 and part not in out:
                out.append(part)
    return out


def _apply_edit(content: str, edit) -> tuple[str, Optional[str]]:
    """Exact find/replace. The find must occur EXACTLY once — ambiguity is
    bounced back to the planner instead of guessing."""
    count = content.count(edit.find)
    if count == 0:
        return content, ("`find` text not present in file (exact match "
                         "required, whitespace included)")
    if count > 1:
        return content, f"`find` text matches {count} locations; disambiguate"
    return content.replace(edit.find, edit.replace, 1), None
