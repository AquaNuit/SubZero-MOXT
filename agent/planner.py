"""Planner (Phase 4): turns a goal + context into a structured edit plan.

The planner is a thin, robust layer over a Provider (Phase 1 interface):
it asks for JSON, parses defensively, and retries with a stricter prompt
before giving up as a `LogicError` (kernel classification: the plan was
wrong/unparseable — Recovery's job from there).

Plan shape (the contract the coding worker executes):

```json
{
  "analysis": "what's wrong and why",
  "edits": [{"path": "ops.py", "find": "<exact current text>",
             "replace": "<new text>"}],
  "test_command": "python3 -m unittest test_ops"
}
```

Edits are exact find/replace pairs applied sequentially — deliberately
simpler than diffs: a small model can produce them reliably, and a
mismatch is a clean, feed-back-able error instead of a corrupt file.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from kernel.recovery import LogicError

log = logging.getLogger("agent.planner")


@dataclass
class EditSpec:
    path: str
    find: str
    replace: str


@dataclass
class Plan:
    analysis: str
    edits: list[EditSpec] = field(default_factory=list)
    test_command: str = ""


class Planner:
    SYSTEM_PROMPT = """You are the planning component of a coding agent.
You receive a goal, code locations from a workspace index, and relevant
code chunks. Respond with a JSON object — and nothing else — of the form:
{
  "analysis": "<what is wrong and why, one paragraph>",
  "edits": [{"path": "<repo-relative path>", "find": "<exact text currently in the file>",
             "replace": "<replacement text>"}],
  "test_command": "<shell command that runs the project's tests>"
}
Rules: `find` must match the current file content EXACTLY (whitespace
included). Prefer small, surgical edits. Always include a test_command."""

    RETRY_PROMPT = ("Your previous reply was not valid. Output ONLY the "
                    "JSON object — no prose, no code fences.")

    def __init__(self, provider, *, model: Optional[str] = None,
                 max_attempts: int = 2):
        self.provider = provider
        self.model = model
        self.max_attempts = max_attempts

    async def plan(self, messages: list[dict[str, str]]) -> Plan:
        """Ask the provider for a plan; parse defensively with one retry."""
        conversation = [{"role": "system", "content": self.SYSTEM_PROMPT},
                        *messages]
        last_error = ""
        for attempt in range(self.max_attempts):
            completion = await self.provider.complete(
                conversation, model=self.model, temperature=0.0,
                max_tokens=4096)
            try:
                return self._parse(completion.content)
            except LogicError as exc:
                last_error = str(exc)
                log.warning("plan parse failed (attempt %d): %s",
                            attempt + 1, exc)
                conversation.append(
                    {"role": "assistant", "content": completion.content})
                conversation.append(
                    {"role": "user", "content": self.RETRY_PROMPT})
        raise LogicError(
            f"planner produced no valid plan after {self.max_attempts} "
            f"attempts: {last_error}")

    async def replan_with_failure(
        self,
        messages: list[dict[str, str]],
        failure_report: str,
    ) -> Plan:
        """Replan after a failed edit/test round, with the failure as
        context (spec §2.3: logic failures replan WITH failure context,
        not the same prompt twice)."""
        followup = [*messages, {
            "role": "user",
            "content": ("The previous plan failed. Failure details:\n"
                        f"{failure_report}\n\nProduce a CORRECTED plan "
                        "(a genuinely different fix, not the same edit "
                        "again).")}]
        return await self.plan(followup)

    # -------------------------------------------------------------- parsing

    def _parse(self, content: str) -> Plan:
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise LogicError("no JSON object in planner output")
        try:
            data: dict[str, Any] = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LogicError(f"planner JSON invalid: {exc}") from exc

        edits: list[EditSpec] = []
        raw_edits = data.get("edits", [])
        if not isinstance(raw_edits, list):
            raise LogicError("'edits' must be a list")
        for i, raw in enumerate(raw_edits):
            try:
                edit = EditSpec(
                    path=str(raw["path"]),
                    find=str(raw["find"]),
                    replace=str(raw["replace"]),
                )
            except (KeyError, TypeError) as exc:
                raise LogicError(
                    f"edit #{i} missing path/find/replace: {exc}") from exc
            if not edit.path or not edit.find:
                raise LogicError(f"edit #{i} has empty path or find")
            edits.append(edit)

        test_command = str(data.get("test_command", "")).strip()
        if not test_command:
            raise LogicError("plan must include a test_command")
        return Plan(analysis=str(data.get("analysis", "")),
                    edits=edits, test_command=test_command)
