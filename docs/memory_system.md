# Memory System

**Status: Planned (Phase 2).** Distinct modules, not one blob:
`memory/working_memory.py`, `memory/long_term.py`, `memory/compression.py`,
`memory/retrieval.py`, `memory/vector_store.py`,
`memory/workspace_indexer.py` (spec §5).

## Storage choices (fixed by the hardware budget)

- **Structured memory + task graph:** SQLite (already the kernel store).
- **Embeddings:** `sqlite-vec` (preferred — one more table in the same
  file, zero new services) or `chromadb` in local persistent mode.
  Explicitly **not** a separate vector-DB server — nothing extra to run on
  a 16GB single-user machine.

## Retrieval sources (RAG), pulled before each LLM call

1. **Workspace embeddings** — chunks of the project's own files, kept fresh
   by the workspace indexer (below).
2. **Prior-decision memory** — "already tried X, failed because Y" records
   (sourced from the structured subtask summaries, see
   `context_management.md`). This is what stops the agent re-walking dead
   ends across subtasks and across sessions.
3. **Project memory** — persistent facts about the current project/target:
   language, build system, prior findings. Written when discovered, read
   at task start.

Retrieval returns top-k chunks with source references; working memory
assembles goal + parent summary + these chunks — nothing else.

## Workspace Indexer (own module, spec §5.1)

The thing that stops the agent re-reading the same files every task.

- **File index:** path, language, content hash, last-embedded-at. Re-embed
  only on hash change — never re-embed because a task merely touched a file.
- **Symbol graph:** functions/classes/imports and their references, built
  per language via tree-sitter (fast, incremental, no compiler frontend).
- **Dependency graph:** module/package import relationships, so "what
  breaks if I change this file" is a graph query, not an LLM call.
- **Incremental updates:** mtime/hash polling (simple is fine at this
  scale; adopt inotify only if polling proves insufficient). A changed file
  updates only its own subgraph.

A coding subagent queries the indexer **before** asking an LLM where to
make a change — cheaper, deterministic, and keeps that question out of the
token budget entirely. Phase 4's acceptance test (fix a seeded bug using
the indexer instead of a fresh full-repo scan) depends on this.

## Phase 2 acceptance tests (spec §10)

- Force a compression pass on a long task → active context stays bounded.
- Touch one file in an indexed tree → only that file's subgraph is updated
  (assert via file-index hashes and symbol-graph diff counts).
