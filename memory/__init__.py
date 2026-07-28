"""Memory & context system (spec §5): distinct modules, not one blob.

- `vector_store` — namespaced embeddings in SQLite + pluggable embedder
- `workspace_indexer` — file index, symbol graph, dependency graph
- `long_term` — decision memory + project memory
- `compression` — structured subtask summaries, transcript store, mid-task compression
- `working_memory` — minimal context assembly + context-pressure monitor
- `retrieval` — top-k merge across workspace / decision / project sources
"""

from .vector_store import HashEmbedder, VectorHit, VectorStore
from .workspace_indexer import ScanReport, SymbolHit, WorkspaceIndexer
from .long_term import DecisionMemory, ProjectMemory
from .compression import (
    CompressedSummary,
    HeuristicSummarizer,
    LLMSummarizer,
    TranscriptStore,
    mid_task_compress,
)
from .working_memory import (
    ContextPressureMonitor,
    HeuristicTokenCounter,
    PressureReport,
    WorkingMemory,
)
from .retrieval import RetrievedChunk, Retriever

__all__ = [
    "HashEmbedder",
    "VectorHit",
    "VectorStore",
    "ScanReport",
    "SymbolHit",
    "WorkspaceIndexer",
    "DecisionMemory",
    "ProjectMemory",
    "CompressedSummary",
    "HeuristicSummarizer",
    "LLMSummarizer",
    "TranscriptStore",
    "mid_task_compress",
    "ContextPressureMonitor",
    "HeuristicTokenCounter",
    "PressureReport",
    "WorkingMemory",
    "RetrievedChunk",
    "Retriever",
]
