"""Vector store (spec §5) — embeddings behind one interface, stdlib-only.

Design decisions (recorded per handoff discipline):
- **No vector-DB server.** Embeddings live in the same SQLite file as the
  rest of the kernel state, as packed float32 blobs. Cosine similarity is
  computed in Python at query time — fine for the tens of thousands of
  chunks a single-user workspace produces. If Phase 7 profiling says
  otherwise, `sqlite-vec` slots in behind this same interface without
  callers changing.
- **The embedder is pluggable.** Phase 2 ships `HashEmbedder`, a
  deterministic stdlib embedder (signed feature hashing over word tokens —
  the classic hashing-vectorizer trick). It needs no model, no network,
  and no VRAM, which keeps every test hermetic. On AT's laptop a real
  embedding model (e.g. Ollama `nomic-embed-text`, <1GB VRAM) implements
  the same 2-method protocol and drops in without touching callers.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from kernel.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    namespace   TEXT NOT NULL,
    ref         TEXT NOT NULL,
    vector      BLOB NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    embedded_at REAL NOT NULL,
    PRIMARY KEY (namespace, ref)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_ns ON embeddings(namespace);
"""


class Embedder(Protocol):
    """The whole embedding contract. Two members, that's it."""

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


class HashEmbedder:
    """Deterministic stdlib embedder: signed feature hashing + L2 norm.

    Texts sharing vocabulary land near each other (cosine), which is all
    the retrieval layer needs for keyword-grade recall. Not semantic — a
    real model replaces it later — but stable, fast, and testable.
    """

    def __init__(self, dimension: int = 512):
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    PROBES = 2  # buckets per token: one unlucky collision can't zero a term

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dimension
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha1(token.encode()).digest()
            weight = 1.0 / self.PROBES
            for probe in range(self.PROBES):
                off = probe * 5
                bucket = int.from_bytes(digest[off:off + 4], "little") % self._dimension
                sign = 1.0 if digest[off + 4] & 1 else -1.0  # signed hashing
                vec[bucket] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


@dataclass
class VectorHit:
    ref: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _pack(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def _cosine(a: list[float], b: list[float]) -> float:
    # Vectors from embedders are L2-normalized; be defensive anyway.
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / math.sqrt(na * nb)


class VectorStore:
    """Namespaced embedding store in the kernel SQLite DB."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = connect(self.db_path)
        self._conn.executescript(SCHEMA)

    def upsert(
        self,
        namespace: str,
        ref: str,
        vector: list[float],
        *,
        metadata: Optional[dict[str, Any]] = None,
        embedded_at: Optional[float] = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO embeddings (namespace, ref, vector, metadata, embedded_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(namespace, ref) DO UPDATE SET
                 vector = excluded.vector, metadata = excluded.metadata,
                 embedded_at = excluded.embedded_at""",
            (namespace, ref, _pack(vector), json.dumps(metadata or {}),
             embedded_at if embedded_at is not None else time.time()),
        )
        self._conn.commit()

    def delete(self, namespace: str, ref: str) -> None:
        self._conn.execute(
            "DELETE FROM embeddings WHERE namespace = ? AND ref = ?",
            (namespace, ref),
        )
        self._conn.commit()

    def delete_where_ref_prefix(self, namespace: str, prefix: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM embeddings WHERE namespace = ? AND ref LIKE ?",
            (namespace, f"{prefix}%"),
        )
        self._conn.commit()
        return cur.rowcount

    def search(
        self,
        namespace: str,
        query_vector: list[float],
        *,
        k: int = 5,
        min_score: float = 0.0,
    ) -> list[VectorHit]:
        """Brute-force cosine top-k within a namespace."""
        rows = self._conn.execute(
            "SELECT ref, vector, metadata FROM embeddings WHERE namespace = ?",
            (namespace,),
        ).fetchall()
        scored = []
        for row in rows:
            score = _cosine(query_vector, _unpack(row["vector"]))
            if score >= min_score:
                scored.append(
                    VectorHit(ref=row["ref"], score=score,
                              metadata=json.loads(row["metadata"]))
                )
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]

    def count(self, namespace: Optional[str] = None) -> int:
        if namespace is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM embeddings WHERE namespace = ?",
                (namespace,),
            ).fetchone()
        return int(row["n"])

    def refs(self, namespace: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT ref FROM embeddings WHERE namespace = ? ORDER BY ref",
            (namespace,),
        ).fetchall()
        return [r["ref"] for r in rows]

    def close(self) -> None:
        self._conn.close()
