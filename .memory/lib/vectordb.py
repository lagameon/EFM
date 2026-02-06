"""
EF Memory V2 — Vector Database

SQLite-based vector storage with FTS5 full-text search.
Pure Python cosine similarity — no numpy, no native extensions.

Storage:
- vectors table: entry_id → embedding blob (struct-packed float32)
- fts_entries:   FTS5 virtual table for BM25 keyword search
- sync_state:    tracks incremental sync cursor

Performance: brute-force cosine over 5000 entries × 768 dims < 10ms.
"""

import math
import sqlite3
import struct
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("efm.vectordb")


# ---------------------------------------------------------------------------
# Vector math (pure Python)
# ---------------------------------------------------------------------------

def pack_vector(vec: List[float]) -> bytes:
    """Pack a list of floats into a binary blob (float32)."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes, dimensions: int) -> List[float]:
    """Unpack a binary blob into a list of floats."""
    return list(struct.unpack(f"{dimensions}f", blob))


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    Returns a value in [-1, 1]. Higher = more similar.
    """
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for av, bv in zip(a, b):
        dot += av * bv
        norm_a += av * av
        norm_b += bv * bv
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


# ---------------------------------------------------------------------------
# VectorDB
# ---------------------------------------------------------------------------

class VectorDB:
    """
    SQLite-based vector storage with optional FTS5 support.

    Tables:
    - vectors:     entry embeddings (struct-packed float32 blobs)
    - fts_entries: FTS5 full-text search index
    - sync_state:  incremental sync tracking
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._fts5_available: bool = True

    # --- Lifecycle ---

    def open(self) -> None:
        """Open or create the database."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        assert self._conn is not None, "Database not open. Call open() first."

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS vectors (
                entry_id    TEXT PRIMARY KEY,
                text_hash   TEXT NOT NULL,
                provider    TEXT NOT NULL,
                model       TEXT NOT NULL,
                dimensions  INTEGER NOT NULL,
                embedding   BLOB NOT NULL,
                deprecated  INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vectors_deprecated
            ON vectors(deprecated)
        """)

        # FTS5 — graceful fallback if not available
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_entries
                USING fts5(
                    entry_id UNINDEXED,
                    title,
                    text,
                    tags
                )
            """)
            self._fts5_available = True
        except sqlite3.OperationalError:
            logger.warning("FTS5 not available in this SQLite build. BM25 search disabled.")
            self._fts5_available = False

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        self._conn.commit()

    # --- Vector operations ---

    def upsert_vector(
        self,
        entry_id: str,
        text_hash: str,
        provider: str,
        model: str,
        dimensions: int,
        embedding: List[float],
        deprecated: bool = False,
    ) -> None:
        """Insert or update a vector embedding."""
        now = datetime.now(timezone.utc).isoformat()
        blob = pack_vector(embedding)
        self._conn.execute(
            """
            INSERT INTO vectors (entry_id, text_hash, provider, model,
                                 dimensions, embedding, deprecated, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                text_hash  = excluded.text_hash,
                provider   = excluded.provider,
                model      = excluded.model,
                dimensions = excluded.dimensions,
                embedding  = excluded.embedding,
                deprecated = excluded.deprecated,
                updated_at = excluded.updated_at
            """,
            (entry_id, text_hash, provider, model, dimensions,
             blob, int(deprecated), now, now),
        )
        self._conn.commit()

    def get_vector(self, entry_id: str) -> Optional[List[float]]:
        """Get the embedding vector for an entry, or None."""
        row = self._conn.execute(
            "SELECT embedding, dimensions FROM vectors WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            return None
        return unpack_vector(row[0], row[1])

    def has_vector(self, entry_id: str) -> bool:
        """Check if a vector exists for the given entry."""
        row = self._conn.execute(
            "SELECT 1 FROM vectors WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        return row is not None

    def needs_update(self, entry_id: str, text_hash: str) -> bool:
        """Check if the vector needs updating (hash mismatch or missing)."""
        row = self._conn.execute(
            "SELECT text_hash FROM vectors WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            return True  # Missing — needs creation
        return row[0] != text_hash  # Hash changed — needs update

    def mark_deprecated(self, entry_id: str) -> None:
        """Mark a vector as deprecated (excluded from search, kept for dedup)."""
        self._conn.execute(
            "UPDATE vectors SET deprecated = 1, updated_at = ? WHERE entry_id = ?",
            (datetime.now(timezone.utc).isoformat(), entry_id),
        )
        self._conn.commit()

    def delete_vector(self, entry_id: str) -> None:
        """Delete a vector entirely."""
        self._conn.execute("DELETE FROM vectors WHERE entry_id = ?", (entry_id,))
        self._conn.commit()

    # --- Vector search ---

    def search_vectors(
        self,
        query_vec: List[float],
        limit: int = 10,
        exclude_deprecated: bool = True,
    ) -> List[Tuple[str, float]]:
        """
        Brute-force cosine similarity search over all vectors.

        Returns list of (entry_id, similarity_score) sorted by score descending.
        """
        where = "WHERE deprecated = 0" if exclude_deprecated else ""
        rows = self._conn.execute(
            f"SELECT entry_id, embedding, dimensions FROM vectors {where}"
        ).fetchall()

        results: List[Tuple[str, float]] = []
        for entry_id, blob, dims in rows:
            stored_vec = unpack_vector(blob, dims)
            sim = cosine_similarity(query_vec, stored_vec)
            results.append((entry_id, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    # --- FTS operations ---

    def upsert_fts(self, entry_id: str, title: str, text: str, tags: str) -> None:
        """Insert or update FTS5 index entry."""
        if not self._fts5_available:
            return

        # FTS5 doesn't support UPSERT, so delete+insert
        self._conn.execute(
            "DELETE FROM fts_entries WHERE entry_id = ?", (entry_id,)
        )
        self._conn.execute(
            "INSERT INTO fts_entries (entry_id, title, text, tags) VALUES (?, ?, ?, ?)",
            (entry_id, title, text, tags),
        )
        self._conn.commit()

    def delete_fts(self, entry_id: str) -> None:
        """Delete an FTS entry."""
        if not self._fts5_available:
            return
        self._conn.execute(
            "DELETE FROM fts_entries WHERE entry_id = ?", (entry_id,)
        )
        self._conn.commit()

    def search_fts(self, query: str, limit: int = 10) -> List[Tuple[str, float]]:
        """
        BM25 full-text search via FTS5.

        Returns list of (entry_id, bm25_score) sorted by relevance.
        FTS5 bm25() returns negative values (lower = more relevant),
        so we normalize to positive scores.
        """
        if not self._fts5_available:
            return []

        try:
            rows = self._conn.execute(
                """
                SELECT entry_id, bm25(fts_entries)
                FROM fts_entries
                WHERE fts_entries MATCH ?
                ORDER BY bm25(fts_entries)
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Malformed FTS query — return empty
            return []

        if not rows:
            return []

        # Normalize: bm25 returns negative values, convert to positive [0, 1]
        raw_scores = [abs(row[1]) for row in rows]
        max_score = max(raw_scores) if raw_scores else 1.0
        return [
            (row[0], abs(row[1]) / max_score if max_score > 0 else 0.0)
            for row in rows
        ]

    # --- Sync state ---

    def get_sync_cursor(self) -> Optional[int]:
        """Get the last synced line number, or None if never synced."""
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = 'last_line'",
        ).fetchone()
        if row is None:
            return None
        return int(row[0])

    def set_sync_cursor(self, line_number: int) -> None:
        """Update the sync cursor to the given line number."""
        self._conn.execute(
            """
            INSERT INTO sync_state (key, value) VALUES ('last_line', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(line_number),),
        )
        self._conn.commit()

    # --- Stats ---

    def stats(self) -> dict:
        """Return database statistics."""
        total = self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        active = self._conn.execute(
            "SELECT COUNT(*) FROM vectors WHERE deprecated = 0"
        ).fetchone()[0]
        deprecated = total - active

        fts_count = 0
        if self._fts5_available:
            fts_count = self._conn.execute(
                "SELECT COUNT(*) FROM fts_entries"
            ).fetchone()[0]

        cursor = self.get_sync_cursor()

        return {
            "vectors_total": total,
            "vectors_active": active,
            "vectors_deprecated": deprecated,
            "fts_entries": fts_count,
            "fts5_available": self._fts5_available,
            "sync_cursor": cursor,
        }
