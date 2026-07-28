"""Workspace Indexer (spec §5.1).

Stops the agent from re-reading the same files every task and answers
"where is X defined" / "what breaks if I change this file" as deterministic
queries instead of LLM calls.

Three structures, all in the kernel SQLite DB:

- **File index** (`workspace_files`): path, language, content hash,
  embedded-at. Re-embed only on hash change — never because a task merely
  touched a file.
- **Symbol graph** (`workspace_symbols`): functions/classes/methods with
  file + line. Python is parsed with `ast` (real parsing, stdlib); other
  languages use conservative regex patterns — upgradeable to tree-sitter
  later behind the same tables (tree-sitter is a new dependency and must
  be justified when added).
- **Dependency graph** (`workspace_imports`): file → imported module.
  `dependents_of(path)` answers "what breaks if I change this file" as a
  graph query.

**Incremental updates** (Phase 2 acceptance): `scan()` polls mtime+hash and
rewrites only the changed/added/removed files' rows and embedding chunks —
asserted by `tests/test_workspace_indexer.py`.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from kernel.db import connect
from .vector_store import Embedder, VectorStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_files (
    root        TEXT NOT NULL,
    path        TEXT NOT NULL,
    language    TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    mtime       REAL NOT NULL,
    size        INTEGER NOT NULL,
    embedded_at REAL,
    PRIMARY KEY (root, path)
);
CREATE TABLE IF NOT EXISTS workspace_symbols (
    root      TEXT NOT NULL,
    file_path TEXT NOT NULL,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL,
    line      INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (root, file_path, name, kind, line)
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON workspace_symbols(root, name);
CREATE TABLE IF NOT EXISTS workspace_imports (
    root      TEXT NOT NULL,
    file_path TEXT NOT NULL,
    module    TEXT NOT NULL,
    PRIMARY KEY (root, file_path, module)
);
CREATE INDEX IF NOT EXISTS idx_imports_module ON workspace_imports(root, module);
"""

NAMESPACE = "workspace"

SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "node_modules", "state",
    ".pytest_cache", ".mypy_cache", "dist", "build", ".idea", ".vscode",
})

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".java": "java",
    ".sh": "bash",
    ".md": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".sql": "sql", ".html": "html", ".css": "css",
}

MAX_FILE_BYTES = 512 * 1024       # skip huge/generated files
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200

# Conservative multi-language symbol patterns (Python uses ast instead).
_SYMBOL_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "javascript": [
        (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
        (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", "function"),
    ],
    "typescript": [
        (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
        (r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(", "function"),
    ],
    "go": [
        (r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", "function"),
        (r"^type\s+(\w+)\s+(?:struct|interface)", "class"),
    ],
    "rust": [
        (r"^\s*(?:pub\s+)?fn\s+(\w+)", "function"),
        (r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl)\s+(\w+)", "class"),
    ],
    "c": [
        (r"^\w[\w\s\*]*\s+(\w+)\s*\([^;]*\)\s*\{", "function"),
    ],
    "cpp": [
        (r"^\w[\w\s\*:&<>]*\s+(\w+)\s*\([^;]*\)\s*(?:const\s*)?\{", "function"),
        (r"^\s*(?:class|struct)\s+(\w+)", "class"),
    ],
    "java": [
        (r"^\s*(?:public|private|protected|static|final|\s)+\w[\w<>\[\]]*\s+(\w+)\s*\([^;]*\)\s*\{", "function"),
        (r"^\s*(?:public\s+)?(?:class|interface|enum)\s+(\w+)", "class"),
    ],
    "bash": [
        (r"^(\w+)\s*\(\)\s*\{", "function"),
    ],
}
_IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "javascript": [re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
                   re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")],
    "typescript": [re.compile(r"from\s+['\"]([^'\"]+)['\"]")],
    "go": [re.compile(r"^\s*\"([\w./-]+)\"", re.M)],
    "rust": [re.compile(r"^\s*use\s+([\w:]+)")],
    "c": [re.compile(r"#include\s+[<\"]([^>\"]+)[>\"]")],
    "cpp": [re.compile(r"#include\s+[<\"]([^>\"]+)[>\"]")],
    "java": [re.compile(r"^\s*import\s+([\w.]+)")],
}


@dataclass
class Symbol:
    name: str
    kind: str  # "function" | "class" | "method"
    line: int
    signature: str = ""


@dataclass
class ScanReport:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    chunks_embedded: int = 0
    symbols_written: int = 0
    skipped: int = 0


@dataclass
class SymbolHit:
    file_path: str
    name: str
    kind: str
    line: int
    signature: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunks(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text] if text.strip() else []
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + CHUNK_CHARS])
        start += CHUNK_CHARS - CHUNK_OVERLAP
    return out


class WorkspaceIndexer:
    """Incremental index of a workspace tree. One instance per thread."""

    def __init__(
        self,
        root: str | Path,
        db_path: str | Path,
        vector_store: VectorStore,
        embedder: Embedder,
    ):
        self.root = str(Path(root).resolve())
        self.db_path = str(db_path)
        self.vs = vector_store
        self.embedder = embedder
        self._conn = connect(self.db_path)
        self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ scan

    def scan(self) -> ScanReport:
        """Poll the tree; rewrite only added/changed/removed files' subgraphs."""
        report = ScanReport()
        seen: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                ext = Path(name).suffix.lower()
                if ext not in LANGUAGE_BY_EXT:
                    report.skipped += 1
                    continue
                full = Path(dirpath) / name
                rel = str(full.relative_to(self.root))
                seen.add(rel)
                try:
                    data = full.read_bytes()
                except OSError:
                    report.skipped += 1
                    continue
                if len(data) > MAX_FILE_BYTES:
                    report.skipped += 1
                    continue
                digest = _sha256(data)
                prior = self._file_row(rel)
                if prior and prior["sha256"] == digest:
                    report.unchanged += 1
                    continue  # hash unchanged -> subgraph untouched
                self._index_file(rel, data, full.stat().st_mtime, report)
                (report.added if prior is None else report.changed).append(rel)

        for rel in self._indexed_paths() - seen:
            self._remove_file(rel)
            report.removed.append(rel)
        return report

    def _index_file(self, rel: str, data: bytes, mtime: float,
                    report: ScanReport) -> None:
        language = LANGUAGE_BY_EXT[Path(rel).suffix.lower()]
        text = data.decode("utf-8", errors="replace")
        now = time.time()

        # Symbols + imports (the file's subgraph is fully rewritten).
        self._conn.execute(
            "DELETE FROM workspace_symbols WHERE root = ? AND file_path = ?",
            (self.root, rel))
        self._conn.execute(
            "DELETE FROM workspace_imports WHERE root = ? AND file_path = ?",
            (self.root, rel))
        symbols, imports = self._extract(language, text)
        for s in symbols:
            self._conn.execute(
                "INSERT OR REPLACE INTO workspace_symbols"
                " (root, file_path, name, kind, line, signature)"
                " VALUES (?,?,?,?,?,?)",
                (self.root, rel, s.name, s.kind, s.line, s.signature))
        for module in imports:
            self._conn.execute(
                "INSERT OR REPLACE INTO workspace_imports (root, file_path, module)"
                " VALUES (?,?,?)", (self.root, rel, module))
        self._conn.execute(
            """INSERT INTO workspace_files
                 (root, path, language, sha256, mtime, size, embedded_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(root, path) DO UPDATE SET
                 language=excluded.language, sha256=excluded.sha256,
                 mtime=excluded.mtime, size=excluded.size,
                 embedded_at=excluded.embedded_at""",
            (self.root, rel, language, _sha256(data), mtime, len(data), now))
        self._conn.commit()
        report.symbols_written += len(symbols)

        # Embedding chunks: delete this file's, embed the new ones.
        self.vs.delete_where_ref_prefix(NAMESPACE, f"{self.root}:{rel}#")
        chunk_texts = _chunks(text)
        vectors = self.embedder.embed(chunk_texts) if chunk_texts else []
        for i, (chunk_text, vector) in enumerate(zip(chunk_texts, vectors)):
            self.vs.upsert(
                NAMESPACE, f"{self.root}:{rel}#chunk{i}", vector,
                metadata={"path": rel, "chunk": i, "text": chunk_text[:400]},
                embedded_at=now,
            )
        report.chunks_embedded += len(vectors)

    def _remove_file(self, rel: str) -> None:
        for table in ("workspace_files", "workspace_symbols", "workspace_imports"):
            col = "path" if table == "workspace_files" else "file_path"
            self._conn.execute(
                f"DELETE FROM {table} WHERE root = ? AND {col} = ?",
                (self.root, rel))
        self._conn.commit()
        self.vs.delete_where_ref_prefix(NAMESPACE, f"{self.root}:{rel}#")

    # -------------------------------------------------------------- extract

    def _extract(self, language: str, text: str) -> tuple[list[Symbol], list[str]]:
        if language == "python":
            return _extract_python(text)
        symbols: list[Symbol] = []
        for pattern, kind in _SYMBOL_PATTERNS.get(language, []):
            for m in re.finditer(pattern, text, re.M):
                symbols.append(Symbol(
                    name=m.group(1), kind=kind,
                    line=text.count("\n", 0, m.start()) + 1,
                    signature=m.group(0).strip()[:120]))
        imports: list[str] = []
        for pattern in _IMPORT_PATTERNS.get(language, []):
            imports.extend(m.group(1) for m in pattern.finditer(text))
        return symbols, imports

    # -------------------------------------------------------------- queries

    def where_is(self, name: str) -> list[SymbolHit]:
        """Deterministic 'where is X defined' — no LLM call needed."""
        rows = self._conn.execute(
            "SELECT file_path, name, kind, line, signature FROM workspace_symbols"
            " WHERE root = ? AND name = ? ORDER BY file_path, line",
            (self.root, name)).fetchall()
        return [SymbolHit(r["file_path"], r["name"], r["kind"], r["line"],
                          r["signature"]) for r in rows]

    def imports_of(self, rel_path: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT module FROM workspace_imports WHERE root = ? AND file_path = ?"
            " ORDER BY module", (self.root, rel_path)).fetchall()
        return [r["module"] for r in rows]

    def dependents_of(self, rel_path: str) -> list[str]:
        """Files that import this file's module — 'what breaks if I change it'.

        Module-name resolution is heuristic: a file `pkg/tool.py` is
        importable as `pkg.tool`, and `tool.py` as `tool`; we match both
        exact and dotted-prefix forms. Good enough for a graph hint; the
        LLM still confirms before editing.
        """
        p = Path(rel_path)
        dotted = rel_path.replace(os.sep, ".")
        if dotted.endswith(".py"):
            dotted = dotted[:-3]
        candidates = {dotted, p.stem}
        hits: set[str] = set()
        for module in candidates:
            rows = self._conn.execute(
                "SELECT DISTINCT file_path FROM workspace_imports"
                " WHERE root = ? AND (module = ? OR module LIKE ?)",
                (self.root, module, module + ".%")).fetchall()
            hits.update(r["file_path"] for r in rows)
        hits.discard(rel_path)
        return sorted(hits)

    def search_code(self, query: str, *, k: int = 5, min_score: float = 0.05):
        """Semantic-ish search over workspace chunks via the vector store."""
        vector = self.embedder.embed([query])[0]
        return self.vs.search(NAMESPACE, vector, k=k, min_score=min_score)

    def file_record(self, rel_path: str) -> Optional[dict]:
        row = self._file_row(rel_path)
        return dict(row) if row else None

    def indexed_files(self) -> list[str]:
        return sorted(self._indexed_paths())

    # -------------------------------------------------------------- internals

    def _file_row(self, rel: str):
        return self._conn.execute(
            "SELECT * FROM workspace_files WHERE root = ? AND path = ?",
            (self.root, rel)).fetchone()

    def _indexed_paths(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT path FROM workspace_files WHERE root = ?", (self.root,)).fetchall()
        return {r["path"] for r in rows}

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------ Python parsing

def _extract_python(text: str) -> tuple[list[Symbol], list[str]]:
    """Real parsing via ast — accurate symbols + imports for Python."""
    symbols: list[Symbol] = []
    imports: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return symbols, imports

    def visit(node, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                try:
                    signature = f"def {child.name}({ast.unparse(child.args)})"
                except Exception:  # noqa: BLE001 — signature is best-effort
                    signature = f"def {child.name}(...)"
                symbols.append(Symbol(
                    child.name,
                    "method" if in_class else "function",
                    child.lineno, signature[:120]))
                visit(child, False)  # nested defs are functions
            elif isinstance(child, ast.ClassDef):
                symbols.append(Symbol(child.name, "class", child.lineno,
                                      f"class {child.name}"[:120]))
                visit(child, True)
            else:
                visit(child, in_class)

    visit(tree, False)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return symbols, imports
