# Memory System

**Status: BUILT (Phase 2, 2026-07-28).** Distinct modules, not one blob —
all under `memory/`, all state in the same kernel SQLite DB.

## Modules

| Module | File | What it does |
|--------|------|--------------|
| Vector store | `memory/vector_store.py` | Namespaced embeddings as float32 blobs in SQLite; brute-force cosine top-k in Python; pluggable `Embedder` protocol |
| Workspace indexer | `memory/workspace_indexer.py` | File index (path/language/hash/embedded-at), symbol graph, dependency graph, incremental scans |
| Long-term memory | `memory/long_term.py` | `DecisionMemory` ("tried X, failed because Y") + `ProjectMemory` (persistent facts with provenance) |
| Compression | `memory/compression.py` | Structured subtask summaries, external transcript store, mid-task compression |
| Working memory | `memory/working_memory.py` | Minimal context assembly + `ContextPressureMonitor` (70% threshold) |
| Retrieval | `memory/retrieval.py` | `Retriever.gather(query, k)` merges workspace + decision + project sources |

## Storage choices (fixed by the hardware budget)

- **Structured memory + task graph + embeddings:** one SQLite file. No
  vector-DB server, no Redis — nothing extra to run on 16GB RAM.
- **Cosine in Python** over packed float32 blobs: fine for the tens of
  thousands of chunks a single-user workspace produces. If Phase 7
  profiling says otherwise, `sqlite-vec` drops in behind the existing
  interface without callers changing (documented trade-off, not a gap).

## The embedder

`HashEmbedder` (default): deterministic, stdlib, zero VRAM — signed
feature hashing over word tokens, 2 probes per token so one collision
can't zero a term. Keyword-grade recall, not semantic; it exists so every
test is hermetic and the retrieval path is exercisable end-to-end today.

A real embedding model (e.g. Ollama `nomic-embed-text`, <1GB VRAM — keep
the 6GB budget's headroom in mind) implements the same 2-member protocol
(`dimension`, `embed(texts)`) and drops in without caller changes.

## Retrieval sources (merged by `Retriever.gather`)

1. **Project memory** — persistent facts, one chunk when present.
2. **Prior-decision memory** — vector search over decision embeddings;
   decisions are recorded with outcome text ("worked" / "failed because Y")
   so the agent stops re-walking dead ends across subtasks and sessions.
3. **Workspace embeddings** — per-file chunks (`<path>#chunkN` refs,
   ~1200 chars with 200 overlap) kept fresh by the indexer.

Results are score-sorted, deduped by ref, capped at k.

## Workspace Indexer (spec §5.1)

- **File index:** re-embeds only on sha256 change — never because a task
  touched a file. `embedded_at` on unchanged files is provably untouched
  (acceptance test).
- **Symbol graph:** Python via `ast` (real parsing: functions, classes,
  methods with kind + line + signature); JS/TS/Go/Rust/C/C++/Java/Bash via
  conservative regex patterns. Upgrade path to tree-sitter exists behind
  the same tables (new dep — justify when added).
- **Dependency graph:** `workspace_imports` (file → module);
  `dependents_of(path)` answers "what breaks if I change this file" as a
  graph query (heuristic module-name resolution — the LLM still confirms
  before editing).
- **Incremental updates:** mtime+hash poll via `scan()` → `ScanReport`
  (added/changed/removed/unchanged/chunks_embedded/symbols_written). Only
  the changed file's subgraph is rewritten — the Phase 2 acceptance test
  asserts other files' rows and chunk refs are byte-for-byte untouched.
- **Queries:** `where_is(name)` (deterministic "where is X defined" — no
  LLM call), `imports_of`, `dependents_of`, `search_code` (vector).

Coding subagents (Phase 4) query the indexer **before** asking an LLM
where to change code — cheaper, deterministic, zero token spend.

## Acceptance (spec §10, Phase 2) — both pass

- `tests/test_memory_integration.py`: a scheduler-run 40-turn task in a
  400-token window forced 3+ mid-task compressions; active context after
  every compression was below the 70% threshold (bounded); summaries kept
  the structured shape; transcript stored externally, structured summary
  to parent.
- `tests/test_workspace_indexer.py::test_acceptance_only_changed_files_subgraph_updates`:
  modifying one file re-embedded exactly that file's chunks; other files'
  index rows and chunk refs untouched.
