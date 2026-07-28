"""Long-term memory (spec §5): prior-decision memory + project memory.

- **Decision memory** — "already tried X, failed because Y" records. This
  is what stops the agent re-walking dead ends across subtasks and across
  sessions. Stored as rows AND embedded for retrieval.
- **Project memory** — persistent facts about the current project/target
  (language, build system, prior findings). Simple key/value with a source
  so the agent can judge provenance later.

Both are plain SQLite — structured, filterable, no prose blobs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kernel.db import connect
from .vector_store import Embedder, VectorHit, VectorStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT,
    decision   TEXT NOT NULL,
    context    TEXT NOT NULL DEFAULT '',
    outcome    TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_task ON decisions(task_id);
CREATE TABLE IF NOT EXISTS project_facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
"""

DECISIONS_NAMESPACE = "decisions"


@dataclass
class Decision:
    id: int
    task_id: Optional[str]
    decision: str
    context: str
    outcome: str
    created_at: float


class DecisionMemory:
    def __init__(self, db_path: str | Path, vector_store: VectorStore,
                 embedder: Embedder):
        self.db_path = str(db_path)
        self.vs = vector_store
        self.embedder = embedder
        self._conn = connect(self.db_path)
        self._conn.executescript(SCHEMA)

    def record(self, decision: str, *, task_id: Optional[str] = None,
               context: str = "", outcome: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO decisions (task_id, decision, context, outcome, created_at)"
            " VALUES (?,?,?,?,?)",
            (task_id, decision, context, outcome, time.time()))
        self._conn.commit()
        decision_id = int(cur.lastrowid)
        text = f"{decision} {context} {outcome}".strip()
        self.vs.upsert(
            DECISIONS_NAMESPACE, f"decision:{decision_id}",
            self.embedder.embed([text])[0],
            metadata={"decision": decision[:200], "outcome": outcome[:200]},
        )
        return decision_id

    def search(self, query: str, *, k: int = 3,
               min_score: float = 0.05) -> list[VectorHit]:
        vector = self.embedder.embed([query])[0]
        return self.vs.search(DECISIONS_NAMESPACE, vector, k=k,
                              min_score=min_score)

    def for_task(self, task_id: str) -> list[Decision]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE task_id = ? ORDER BY id",
            (task_id,)).fetchall()
        return [Decision(int(r["id"]), r["task_id"], r["decision"],
                         r["context"], r["outcome"], r["created_at"])
                for r in rows]

    def recent(self, n: int = 20) -> list[Decision]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [Decision(int(r["id"]), r["task_id"], r["decision"],
                         r["context"], r["outcome"], r["created_at"])
                for r in rows]

    def close(self) -> None:
        self._conn.close()


class ProjectMemory:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = connect(self.db_path)
        self._conn.executescript(SCHEMA)

    def set_fact(self, key: str, value: str, *, source: str = "") -> None:
        self._conn.execute(
            """INSERT INTO project_facts (key, value, source, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, source = excluded.source,
                 updated_at = excluded.updated_at""",
            (key, value, source, time.time()))
        self._conn.commit()

    def get_fact(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM project_facts WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def all_facts(self) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM project_facts ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM project_facts WHERE key = ?", (key,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
