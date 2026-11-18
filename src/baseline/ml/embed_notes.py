"""Delay notes as vectors in pgvector, so you can search for similar delays.

The default embedder is a feature-hashing model: it runs offline, needs no downloads, and
gives the same vector every time (good for tests and idempotency). It matches on shared
words and word pairs, not deep meaning. To get true semantic search, swap in a sentence
embedding model and change EMBEDDING_DIM (and the vector column size in migration 0005).
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid

from sqlalchemy import Connection, text

EMBEDDING_DIM = 256
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "on", "to", "by", "for", "and", "in", "at", "is", "was"}


class HashingEmbedder:
    dim = EMBEDDING_DIM

    def _features(self, text_: str) -> list[str]:
        words = [w for w in _WORD.findall(text_.lower()) if w not in _STOP]
        # Words, word pairs, and light stemming so "delivery"/"deliveries" overlap.
        stems = [w[:6] for w in words]
        return words + stems + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]

    def embed(self, text_: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat in self._features(text_):
            h = int.from_bytes(hashlib.sha1(feat.encode()).digest()[:8], "big")
            vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 6) for v in vec]


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def embed_snapshot_notes(conn: Connection, tenant_id: str, snapshot_id: uuid.UUID) -> int:
    """Embed every delay note in one snapshot. Safe to re-run (replaces existing rows)."""
    embedder = HashingEmbedder()
    conn.execute(
        text("DELETE FROM delay_note_embedding WHERE snapshot_id = :s"), {"s": snapshot_id}
    )
    rows = conn.execute(
        text(
            "SELECT a.activity_id, a.delay_note, s.project_id, s.data_date "
            "FROM activity a JOIN snapshot s USING (snapshot_id) "
            "WHERE a.snapshot_id = :s AND a.delay_note IS NOT NULL"
        ),
        {"s": snapshot_id},
    ).all()
    for r in rows:
        conn.execute(
            text(
                "INSERT INTO delay_note_embedding "
                "(tenant_id, snapshot_id, activity_id, project_id, data_date, note, embedding) "
                "VALUES (:t, :s, :a, :p, :d, :n, CAST(:e AS vector))"
            ),
            {
                "t": tenant_id, "s": snapshot_id, "a": r.activity_id, "p": r.project_id,
                "d": r.data_date, "n": r.delay_note,
                "e": to_pgvector(embedder.embed(r.delay_note)),
            },
        )  # fmt: skip
    return len(rows)


def search_notes(
    conn: Connection, query: str, k: int = 10, project_id: str | None = None
) -> list[dict]:
    """Most similar distinct delay notes (cosine similarity), across the tenant's projects.

    The same note often repeats across weeks and activities; each one is shown once,
    with the most recent place it appeared.
    """
    vec = to_pgvector(HashingEmbedder().embed(query))
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT ON (note)
                   project_id, activity_id, note, data_date,
                   1 - (embedding <=> CAST(:q AS vector)) AS similarity
            FROM delay_note_embedding
            WHERE (CAST(:p AS text) IS NULL OR project_id = :p)
            ORDER BY note, data_date DESC
            """
        ),
        {"q": vec, "p": project_id},
    ).mappings()
    return sorted((dict(r) for r in rows), key=lambda r: -r["similarity"])[:k]