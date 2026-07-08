# -*- coding: utf-8 -*-
"""SQLite metadata and FTS5 sidecar for search/index validation.

This module is intentionally side-by-side with the existing JSONL/vector store.
All application integrations should treat writes as best-effort so SQLite cannot
break embedding or vector search while Phase 4 is being validated.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from config_manager import load_config
from runtime_paths import runtime_path


DEFAULT_DB_PATH = runtime_path("metadata_index.sqlite3")


def _expand_config_path(path: str) -> str:
    value = os.path.expandvars(path or DEFAULT_DB_PATH)
    return value.replace("%LOCALAPPDATA%", os.environ.get("LOCALAPPDATA", ""))


def get_db_path() -> str:
    env_path = os.environ.get("SQLITE_INDEX_PATH")
    if env_path:
        return env_path
    cfg = load_config().get("metadata_index", {})
    return _expand_config_path(cfg.get("path") or DEFAULT_DB_PATH)


def is_enabled() -> bool:
    env_value = os.environ.get("SQLITE_INDEX_ENABLED")
    if env_value is not None:
        return env_value.strip().lower() in ("1", "true", "yes", "y")
    return bool(load_config().get("metadata_index", {}).get("enabled", False))


def make_doc_id(source_path: str) -> str:
    normalized = os.path.normcase(os.path.abspath(source_path or ""))
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[str] = None) -> str:
    path = db_path or get_db_path()
    with closing(_connect(path)) as conn:
        with conn:
            conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL UNIQUE,
                file_ext TEXT,
                file_size INTEGER,
                last_modified REAL,
                indexed_at REAL,
                chunk_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error_detail TEXT
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT,
                UNIQUE(doc_id, chunk_index)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content,
                chunk_id UNINDEXED,
                doc_id UNINDEXED,
                source_path UNINDEXED
            );

            CREATE TABLE IF NOT EXISTS index_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_time REAL NOT NULL,
                source_path TEXT,
                status TEXT NOT NULL,
                detail TEXT
            );
            """
            )
    return path


def _file_info(source_path: str) -> Dict[str, object]:
    try:
        stat = os.stat(source_path)
        return {"file_size": stat.st_size, "last_modified": stat.st_mtime}
    except OSError:
        return {"file_size": None, "last_modified": None}


def record_status(source_path: str, status: str, detail: str = "", db_path: Optional[str] = None) -> None:
    init_db(db_path)
    doc_id = make_doc_id(source_path)
    info = _file_info(source_path)
    now = time.time()
    with closing(_connect(db_path)) as conn:
        with conn:
            conn.execute(
                """
            INSERT INTO documents(doc_id, source_path, file_ext, file_size, last_modified, indexed_at, status, error_detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                source_path=excluded.source_path,
                file_ext=excluded.file_ext,
                file_size=excluded.file_size,
                last_modified=excluded.last_modified,
                indexed_at=excluded.indexed_at,
                status=excluded.status,
                error_detail=excluded.error_detail
            """,
                (
                    doc_id,
                    source_path,
                    os.path.splitext(source_path)[1].lower(),
                    info["file_size"],
                    info["last_modified"],
                    now,
                    status,
                    detail[:1000] if detail else "",
                ),
            )
            conn.execute(
                "INSERT INTO index_events(event_time, source_path, status, detail) VALUES (?, ?, ?, ?)",
                (now, source_path, status, detail[:1000] if detail else ""),
            )


def upsert_chunks(source_path: str, chunks: Iterable[Dict], status: str = "ok", db_path: Optional[str] = None) -> int:
    init_db(db_path)
    chunk_rows = list(chunks or [])
    doc_id = make_doc_id(source_path)
    info = _file_info(source_path)
    now = time.time()
    with closing(_connect(db_path)) as conn:
        with conn:
            conn.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute(
                """
            INSERT INTO documents(doc_id, source_path, file_ext, file_size, last_modified, indexed_at, chunk_count, status, error_detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
            ON CONFLICT(doc_id) DO UPDATE SET
                source_path=excluded.source_path,
                file_ext=excluded.file_ext,
                file_size=excluded.file_size,
                last_modified=excluded.last_modified,
                indexed_at=excluded.indexed_at,
                chunk_count=excluded.chunk_count,
                status=excluded.status,
                error_detail=''
            """,
                (
                    doc_id,
                    source_path,
                    os.path.splitext(source_path)[1].lower(),
                    info["file_size"],
                    info["last_modified"],
                    now,
                    len(chunk_rows),
                    status,
                ),
            )
            for index, chunk in enumerate(chunk_rows):
                content = str(chunk.get("content") or "")
                chunk_id = f"{doc_id}:{index}"
                metadata_json = json.dumps(chunk.get("metadata", {}), ensure_ascii=False)
                conn.execute(
                    "INSERT INTO chunks(chunk_id, doc_id, chunk_index, content, metadata_json) VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, doc_id, index, content, metadata_json),
                )
                conn.execute(
                    "INSERT INTO chunks_fts(content, chunk_id, doc_id, source_path) VALUES (?, ?, ?, ?)",
                    (content, chunk_id, doc_id, source_path),
                )
            conn.execute(
                "INSERT INTO index_events(event_time, source_path, status, detail) VALUES (?, ?, ?, ?)",
                (now, source_path, status, f"chunks={len(chunk_rows)}"),
            )
    return len(chunk_rows)


def search_fts(query: str, k: int = 5, db_path: Optional[str] = None) -> List[Dict]:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT f.content, f.source_path, c.metadata_json, bm25(chunks_fts) AS score
            FROM chunks_fts f
            LEFT JOIN chunks c ON c.chunk_id = f.chunk_id
            WHERE chunks_fts MATCH ?
            ORDER BY bm25(chunks_fts)
            LIMIT ?
            """,
            (query, int(k)),
        ).fetchall()
    results = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        results.append({
            "content": row["content"],
            "source": row["source_path"],
            "metadata": metadata,
            "score": float(row["score"]),
            "source_engine": "sqlite_fts5",
        })
    return results


def safe_record_status(source_path: str, status: str, detail: str = "") -> None:
    if not is_enabled():
        return
    try:
        record_status(source_path, status, detail)
    except Exception as exc:
        print(f"[SQLiteIndex] status write skipped: {exc}")


def safe_upsert_chunks(source_path: str, chunks: Iterable[Dict], status: str = "ok") -> None:
    if not is_enabled():
        return
    try:
        upsert_chunks(source_path, chunks, status=status)
    except Exception as exc:
        print(f"[SQLiteIndex] chunk write skipped: {exc}")
