"""Retrieval (spec §5): top-k relevant chunks pulled before each LLM call.

Sources, merged in priority order:
1. **Project memory** — persistent facts (language, build system, prior
   findings). Small by design; included as one chunk when present.
2. **Prior-decision memory** — "already tried X, failed because Y", via
   vector search over decision embeddings.
3. **Workspace embeddings** — code/document chunks from the indexer.

Deterministic and cheap: the indexer's `where_is`/`dependents_of` answer
structural questions without spending tokens at all; this module handles
the fuzzy "what's relevant to this goal" part.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RetrievedChunk:
    source: str  # "project" | "decision" | "workspace"
    ref: str
    text: str
    score: float


class Retriever:
    def __init__(
        self,
        *,
        workspace_indexer=None,
        decision_memory=None,
        project_memory=None,
    ):
        self.indexer = workspace_indexer
        self.decisions = decision_memory
        self.project = project_memory

    def gather(self, query: str, *, k: int = 6) -> list[RetrievedChunk]:
        chunks: list[RetrievedChunk] = []

        if self.project is not None:
            facts = self.project.all_facts()
            if facts:
                text = "\n".join(f"{key}: {value}" for key, value in facts.items())
                chunks.append(RetrievedChunk("project", "facts", text, 1.0))

        if self.decisions is not None:
            for hit in self.decisions.search(query, k=max(1, k // 3)):
                text = f"{hit.metadata.get('decision', '')}"
                if hit.metadata.get("outcome"):
                    text += f" (outcome: {hit.metadata['outcome']})"
                chunks.append(RetrievedChunk(
                    "decision", hit.ref, text, hit.score))

        if self.indexer is not None:
            remaining = max(1, k - len(chunks))
            for hit in self.indexer.search_code(query, k=remaining):
                chunks.append(RetrievedChunk(
                    "workspace", hit.ref,
                    hit.metadata.get("text", ""), hit.score))

        chunks.sort(key=lambda c: c.score, reverse=True)
        # Dedup by ref, cap at k.
        seen: set[str] = set()
        out: list[RetrievedChunk] = []
        for chunk in chunks:
            if chunk.ref in seen:
                continue
            seen.add(chunk.ref)
            out.append(chunk)
            if len(out) >= k:
                break
        return out
