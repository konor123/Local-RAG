# -*- coding: utf-8 -*-
"""Separate SQLite lifecycle catalog for atom vectors and parent chunks."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from typing import Dict, Iterable, List

from runtime_paths import runtime_path


def get_catalog_path() -> str:
    return os.environ.get("ATOM_CATALOG_PATH", runtime_path("atom_catalog.sqlite"))


def _connect(path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or get_catalog_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_catalog(path: str = None) -> None:
    with closing(_connect(path)) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS parents (
            parent_chunk_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            content_fingerprint TEXT NOT NULL,
            indexed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS atoms (
            atom_id TEXT PRIMARY KEY,
            parent_chunk_id TEXT NOT NULL,
            atom_index INTEGER NOT NULL,
            atom_type TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            indexed_at REAL NOT NULL,
            UNIQUE(parent_chunk_id, atom_index)
        );
        CREATE INDEX IF NOT EXISTS idx_atoms_parent_status ON atoms(parent_chunk_id, status);
        """)
        conn.commit()


def stage_documents(parents: Iterable[Dict], atoms: Iterable[Dict], path: str = None) -> None:
    init_catalog(path)
    now = time.time()
    with closing(_connect(path)) as conn, conn:
        for parent in parents or []:
            conn.execute("""INSERT INTO parents(parent_chunk_id, source, content, metadata_json, content_fingerprint, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(parent_chunk_id) DO UPDATE SET source=excluded.source, content=excluded.content,
                metadata_json=excluded.metadata_json, content_fingerprint=excluded.content_fingerprint, indexed_at=excluded.indexed_at""", (
                parent["parent_chunk_id"], parent["source"], parent["content"],
                json.dumps(parent.get("metadata", {}), ensure_ascii=False), parent["content_fingerprint"], now,
            ))
        for atom in atoms or []:
            conn.execute("""INSERT INTO atoms(atom_id, parent_chunk_id, atom_index, atom_type, content, source, status, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, 'staged', ?)
                ON CONFLICT(atom_id) DO UPDATE SET parent_chunk_id=excluded.parent_chunk_id, atom_index=excluded.atom_index,
                atom_type=excluded.atom_type, content=excluded.content, source=excluded.source,
                status=CASE WHEN atoms.status='active' THEN 'active' ELSE 'staged' END, indexed_at=excluded.indexed_at""", (
                atom["atom_id"], atom["parent_chunk_id"], atom["atom_index"], atom["atom_type"], atom["content"], atom["source"], now,
            ))


def activate_atoms(atom_ids: Iterable[str], path: str = None) -> None:
    ids = list(atom_ids or [])
    if not ids:
        return
    init_catalog(path)
    placeholders = ",".join("?" for _ in ids)
    with closing(_connect(path)) as conn, conn:
        conn.execute(f"UPDATE atoms SET status='active' WHERE atom_id IN ({placeholders})", ids)


def get_active_atom_ids(atom_ids: Iterable[str], path: str = None) -> set[str]:
    ids = list(dict.fromkeys(atom_ids or []))
    if not ids:
        return set()
    init_catalog(path)
    placeholders = ",".join("?" for _ in ids)
    with closing(_connect(path)) as conn:
        rows = conn.execute(f"SELECT atom_id FROM atoms WHERE status='active' AND atom_id IN ({placeholders})", ids).fetchall()
    return {row["atom_id"] for row in rows}


def retire_source_except(source: str, parent_chunk_ids: Iterable[str], path: str = None) -> None:
    """Hide old source atoms only after a replacement index generation is active."""
    parent_ids = list(dict.fromkeys(parent_chunk_ids or []))
    init_catalog(path)
    with closing(_connect(path)) as conn, conn:
        if parent_ids:
            placeholders = ",".join("?" for _ in parent_ids)
            conn.execute(
                f"UPDATE atoms SET status='retired' WHERE source=? AND parent_chunk_id NOT IN ({placeholders})",
                [source, *parent_ids],
            )
        else:
            conn.execute("UPDATE atoms SET status='retired' WHERE source=?", (source,))


def get_active_parents(atom_ids: Iterable[str], path: str = None) -> Dict[str, Dict]:
    ids = list(dict.fromkeys(atom_ids or []))
    if not ids:
        return {}
    init_catalog(path)
    placeholders = ",".join("?" for _ in ids)
    sql = f"""SELECT atoms.atom_id, atoms.parent_chunk_id, parents.source, parents.content, parents.metadata_json
        FROM atoms JOIN parents ON parents.parent_chunk_id = atoms.parent_chunk_id
        WHERE atoms.status='active' AND atoms.atom_id IN ({placeholders})"""
    with closing(_connect(path)) as conn:
        rows = conn.execute(sql, ids).fetchall()
    return {row["atom_id"]: {
        "parent_chunk_id": row["parent_chunk_id"], "source": row["source"], "content": row["content"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
    } for row in rows}
