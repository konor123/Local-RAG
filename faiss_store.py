# -*- coding: utf-8 -*-
"""
Vector Store compatibility module.

The public API intentionally keeps the historical faiss_store.py functions so
existing callers do not need to know whether FAISS or TurboVec is active.
"""
from __future__ import annotations

import json
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import Dict, List

from config_manager import load_config
from runtime_paths import runtime_path


VECTOR_DIM = int(os.environ.get("VECTOR_DIM", "384"))
VECTOR_BACKEND = os.environ.get("VECTOR_BACKEND", "faiss").strip().lower() or "faiss"
VECTOR_BACKEND_FALLBACK = os.environ.get("VECTOR_BACKEND_FALLBACK", "").strip().lower()
VECTOR_BACKEND_STRICT = os.environ.get("VECTOR_BACKEND_STRICT", "true").strip().lower() in ("1", "true", "yes", "y")

FAISS_INDEX_DIR = os.environ.get("FAISS_INDEX_DIR", runtime_path("faiss_index"))
TURBOVEC_INDEX_DIR = os.environ.get("TURBOVEC_INDEX_DIR", runtime_path("turbovec_index"))
TURBOVEC_BIT_WIDTH = int(os.environ.get("TURBOVEC_BIT_WIDTH", "4") or 4)

_backend_lock = threading.RLock()
_backend = None
_fallback_active = False
_backend_load_failed_until = 0.0
_backend_last_error = ""


def _embedding_config() -> dict:
    return load_config().get("embedding", {})


def _mb_to_bytes(value, default_mb: int) -> int:
    try:
        mb = int(value)
    except (TypeError, ValueError):
        mb = default_mb
    if mb <= 0:
        return 0
    return mb * 1024 * 1024


def _format_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f}MB"


def _guard_existing_store_size(index_file: str, meta_file: str, backend_name: str) -> None:
    """Refuse eager loading of known-oversized persisted vector stores.

    This check intentionally runs before faiss.read_index()/IdMapIndex.load()
    and before parsing metadata.jsonl, so a bad runtime state cannot trigger a
    repeated memory spike.
    """
    cfg = _embedding_config()
    max_index = _mb_to_bytes(cfg.get("max_index_mb_for_eager_load"), 2048)
    max_meta = _mb_to_bytes(cfg.get("max_metadata_mb_for_eager_load"), 512)

    if max_index and os.path.exists(index_file):
        index_size = os.path.getsize(index_file)
        if index_size > max_index:
            raise RuntimeError(
                f"{backend_name} index file is too large for eager load: "
                f"{_format_mb(index_size)} > {_format_mb(max_index)} ({index_file})"
            )

    if max_meta and os.path.exists(meta_file):
        meta_size = os.path.getsize(meta_file)
        if meta_size > max_meta:
            raise RuntimeError(
                f"{backend_name} metadata file is too large for eager load: "
                f"{_format_mb(meta_size)} > {_format_mb(max_meta)} ({meta_file})"
            )


def _backend_failure_cooldown_seconds() -> int:
    cfg = _embedding_config()
    backoffs = cfg.get("retry_backoff_seconds", [60])
    if isinstance(backoffs, list) and backoffs:
        try:
            return max(1, int(backoffs[0]))
        except (TypeError, ValueError):
            return 60
    return 60


def _normalize_vectors(vectors: List[list]) -> np.ndarray:
    import numpy as np

    arr = np.array(vectors, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr


def _apply_keyword_boost(results: list, query_text: str, k: int) -> list:
    if query_text:
        keywords = query_text.lower().split()
        for item in results:
            content_lower = item.get("content", "").lower()
            hits = sum(1 for kw in keywords if kw in content_lower)
            if hits:
                item["score"] = float(item.get("score", 0.0)) + 0.1 * hits
    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return results[:k]


def _atomic_write_jsonl(path: str, rows: list) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


class VectorBackend(ABC):
    name = "base"

    @abstractmethod
    def load_index(self) -> bool: ...

    @abstractmethod
    def save_index(self) -> None: ...

    @abstractmethod
    def add_documents(self, documents: list) -> None: ...

    @abstractmethod
    def search_similar(self, query_vector: list, query_text: str = "", k: int = 5) -> list: ...

    @abstractmethod
    def get_total_count(self) -> int: ...


class FaissBackend(VectorBackend):
    name = "faiss"

    def __init__(self):
        self.index_dir = FAISS_INDEX_DIR
        self.index_file = os.path.join(self.index_dir, "index.faiss")
        self.meta_file = os.path.join(self.index_dir, "metadata.jsonl")
        self._lock = threading.RLock()
        self._index = None
        self._metadata = []
        self._dirty = False

    def _ensure_dir(self):
        os.makedirs(self.index_dir, exist_ok=True)

    def load_index(self) -> bool:
        with self._lock:
            if self._index is not None:
                return True
            self._ensure_dir()
            index_exists = os.path.exists(self.index_file)
            meta_exists = os.path.exists(self.meta_file)
            if index_exists and meta_exists:
                try:
                    _guard_existing_store_size(self.index_file, self.meta_file, self.name)
                    import faiss

                    self._index = faiss.read_index(self.index_file)
                    self._metadata = []
                    with open(self.meta_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                self._metadata.append(json.loads(line))
                    print(f"[VectorStore:faiss] Loaded index: {self._index.ntotal:,} vectors, {len(self._metadata):,} metadata entries")
                    return True
                except Exception as e:
                    self._index = None
                    self._metadata = []
                    raise RuntimeError(f"FAISS index exists but failed to load; refusing to create an empty replacement: {e}") from e
            if index_exists != meta_exists:
                raise RuntimeError(
                    f"FAISS index is incomplete: index_file_exists={index_exists}, metadata_file_exists={meta_exists}"
                )
            import faiss

            self._index = faiss.IndexFlatIP(VECTOR_DIM)
            self._metadata = []
            print(f"[VectorStore:faiss] Created new index (dim={VECTOR_DIM})")
            return True

    def save_index(self) -> None:
        with self._lock:
            if self._index is None:
                return
            self._ensure_dir()
            import faiss

            faiss.write_index(self._index, self.index_file)
            _atomic_write_jsonl(self.meta_file, self._metadata)
            self._dirty = False
            print(f"[VectorStore:faiss] Saved index: {self._index.ntotal:,} vectors")

    def add_documents(self, documents: list) -> None:
        self.load_index()
        vectors, metas = [], []
        for doc in documents or []:
            vec = doc.get("vector")
            if vec is None or len(vec) != VECTOR_DIM:
                continue
            vectors.append(vec)
            metas.append({
                "content": doc.get("content", ""),
                "source": doc.get("source", "Unknown"),
                "metadata": doc.get("metadata", {}),
            })
        if not vectors:
            return
        vec_array = _normalize_vectors(vectors)
        with self._lock:
            self._index.add(vec_array)
            self._metadata.extend(metas)
            self._dirty = True
            total = self._index.ntotal
        if total % 100 < len(vectors):
            self.save_index()

    def search_similar(self, query_vector: list, query_text: str = "", k: int = 5) -> list:
        self.load_index()
        with self._lock:
            if self._index.ntotal == 0:
                return []
            q_vec = _normalize_vectors([query_vector])
            search_k = min(k * 3, self._index.ntotal)
            scores, indices = self._index.search(q_vec, search_k)
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(self._metadata):
                    continue
                meta = self._metadata[idx]
                results.append({
                    "content": meta.get("content", ""),
                    "source": meta.get("source", "Unknown"),
                    "metadata": meta.get("metadata", {}),
                    "score": float(score),
                })
        return _apply_keyword_boost(results, query_text, k)

    def get_total_count(self) -> int:
        self.load_index()
        with self._lock:
            return self._index.ntotal if self._index else 0


class TurboVecBackend(VectorBackend):
    name = "turbovec"

    def __init__(self):
        self.index_dir = TURBOVEC_INDEX_DIR
        self.index_file = os.path.join(self.index_dir, "index.tvim")
        self.meta_file = os.path.join(self.index_dir, "metadata.jsonl")
        self._lock = threading.RLock()
        self._index = None
        self._metadata_by_id: Dict[int, dict] = {}
        self._next_id = 1
        self._dirty = False

    def _ensure_dir(self):
        os.makedirs(self.index_dir, exist_ok=True)

    def _new_index(self):
        from turbovec import IdMapIndex
        return IdMapIndex(dim=VECTOR_DIM, bit_width=TURBOVEC_BIT_WIDTH)

    def load_index(self) -> bool:
        with self._lock:
            if self._index is not None:
                return True
            self._ensure_dir()
            from turbovec import IdMapIndex
            index_exists = os.path.exists(self.index_file)
            meta_exists = os.path.exists(self.meta_file)
            if index_exists and meta_exists:
                try:
                    _guard_existing_store_size(self.index_file, self.meta_file, self.name)
                    self._index = IdMapIndex.load(self.index_file)
                    self._metadata_by_id = {}
                    max_id = 0
                    with open(self.meta_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            row = json.loads(line)
                            row_id = int(row.get("id", 0))
                            if row_id <= 0:
                                continue
                            self._metadata_by_id[row_id] = {
                                "content": row.get("content", ""),
                                "source": row.get("source", "Unknown"),
                                "metadata": row.get("metadata", {}),
                            }
                            max_id = max(max_id, row_id)
                    if len(self._index) != len(self._metadata_by_id):
                        raise RuntimeError(
                            f"TurboVec index/metadata count mismatch: index={len(self._index)}, metadata={len(self._metadata_by_id)}"
                        )
                    self._next_id = max_id + 1
                    print(f"[VectorStore:turbovec] Loaded index: {len(self._index):,} vectors, {len(self._metadata_by_id):,} metadata entries")
                    return True
                except Exception as e:
                    self._index = None
                    self._metadata_by_id = {}
                    raise RuntimeError(f"TurboVec index exists but failed to load; refusing to create an empty replacement: {e}") from e
            if index_exists != meta_exists:
                raise RuntimeError(
                    f"TurboVec index is incomplete: index_file_exists={index_exists}, metadata_file_exists={meta_exists}"
                )
            self._index = self._new_index()
            self._metadata_by_id = {}
            self._next_id = 1
            print(f"[VectorStore:turbovec] Created new index (dim={VECTOR_DIM}, bit_width={TURBOVEC_BIT_WIDTH})")
            return True

    def save_index(self) -> None:
        with self._lock:
            if self._index is None:
                return
            self._ensure_dir()
            tmp_index_file = f"{self.index_file}.tmp"
            self._index.write(tmp_index_file)
            os.replace(tmp_index_file, self.index_file)
            rows = [
                {"id": row_id, **meta}
                for row_id, meta in sorted(self._metadata_by_id.items())
            ]
            _atomic_write_jsonl(self.meta_file, rows)
            self._dirty = False
            print(f"[VectorStore:turbovec] Saved index: {len(self._index):,} vectors")

    def add_documents(self, documents: list) -> None:
        self.load_index()
        vectors, ids, metas = [], [], []
        for doc in documents or []:
            vec = doc.get("vector")
            if vec is None or len(vec) != VECTOR_DIM:
                continue
            vectors.append(vec)
            metas.append({
                "content": doc.get("content", ""),
                "source": doc.get("source", "Unknown"),
                "metadata": doc.get("metadata", {}),
            })
        if not vectors:
            return
        vec_array = _normalize_vectors(vectors)
        with self._lock:
            start_id = self._next_id
            import numpy as np

            ids = np.arange(start_id, start_id + len(vectors), dtype=np.uint64)
            self._index.add_with_ids(vec_array, ids)
            for row_id, meta in zip(ids.tolist(), metas):
                self._metadata_by_id[int(row_id)] = meta
            self._next_id += len(vectors)
            self._dirty = True
            total = len(self._index)
        if total % 100 < len(vectors):
            self.save_index()

    def search_similar(self, query_vector: list, query_text: str = "", k: int = 5) -> list:
        self.load_index()
        with self._lock:
            if len(self._index) == 0:
                return []
            q_vec = _normalize_vectors([query_vector])
            search_k = min(k * 3, len(self._index))
            scores, ids = self._index.search(q_vec, k=search_k)
            results = []
            for score, row_id in zip(scores[0], ids[0]):
                row_id = int(row_id)
                meta = self._metadata_by_id.get(row_id)
                if not meta:
                    continue
                results.append({
                    "content": meta.get("content", ""),
                    "source": meta.get("source", "Unknown"),
                    "metadata": meta.get("metadata", {}),
                    "score": float(score),
                })
        return _apply_keyword_boost(results, query_text, k)

    def get_total_count(self) -> int:
        self.load_index()
        with self._lock:
            return len(self._index) if self._index is not None else 0


def _make_backend(name: str) -> VectorBackend:
    if name == "faiss":
        return FaissBackend()
    if name == "turbovec":
        return TurboVecBackend()
    raise ValueError(f"Unsupported VECTOR_BACKEND: {name!r}")


def _get_backend() -> VectorBackend:
    global _backend, _fallback_active, _backend_load_failed_until, _backend_last_error
    with _backend_lock:
        if _backend is not None:
            return _backend
        now = time.time()
        if _backend_load_failed_until > now:
            remaining = int(_backend_load_failed_until - now)
            raise RuntimeError(
                f"VectorStore load suppressed for {remaining}s after previous failure: {_backend_last_error}"
            )
        try:
            _backend = _make_backend(VECTOR_BACKEND)
            _backend.load_index()
            _fallback_active = False
            _backend_load_failed_until = 0.0
            _backend_last_error = ""
        except Exception as e:
            _backend = None
            _fallback_active = False
            _backend_last_error = str(e)
            _backend_load_failed_until = time.time() + _backend_failure_cooldown_seconds()
            print(f"[VectorStore:{VECTOR_BACKEND}] Load failed: {e}")
            if not VECTOR_BACKEND_FALLBACK or VECTOR_BACKEND_STRICT:
                raise
            try:
                _backend = _make_backend(VECTOR_BACKEND_FALLBACK)
                _backend.load_index()
                _fallback_active = True
                _backend_load_failed_until = 0.0
                _backend_last_error = ""
                print(f"[VectorStore] Read fallback active: {VECTOR_BACKEND} -> {VECTOR_BACKEND_FALLBACK}")
            except Exception as fallback_error:
                _backend = None
                _fallback_active = False
                _backend_last_error = str(fallback_error)
                _backend_load_failed_until = time.time() + _backend_failure_cooldown_seconds()
                raise
        return _backend


def load_index():
    """Active vector backend load."""
    return _get_backend().load_index()


def save_index():
    """Persist active vector backend."""
    return _get_backend().save_index()


def add_documents(documents: list):
    """Add documents to the active vector backend."""
    backend = _get_backend()
    if _fallback_active and VECTOR_BACKEND != backend.name:
        raise RuntimeError("VectorStore write blocked because fallback backend is active")
    return backend.add_documents(documents)


def search_similar(query_vector: list, query_text: str = "", k: int = 5) -> list:
    """Search similar chunks using the active vector backend."""
    return _get_backend().search_similar(query_vector, query_text, k)


def get_total_count() -> int:
    """Return active vector backend vector count."""
    return _get_backend().get_total_count()


def get_backend_name() -> str:
    """Return active backend name for diagnostics."""
    return _get_backend().name


if __name__ == "__main__":
    import numpy as np

    print(f"=== Vector Store Test ({VECTOR_BACKEND}) ===")
    test_docs = [
        {
            "content": "유도등 설치 기준에 대한 문서입니다.",
            "source": "test_doc_1.pdf",
            "vector": np.random.randn(VECTOR_DIM).tolist(),
            "metadata": {"page": 1},
        },
        {
            "content": "소방 설비 점검 절차를 설명합니다.",
            "source": "test_doc_2.pdf",
            "vector": np.random.randn(VECTOR_DIM).tolist(),
            "metadata": {"page": 1},
        },
    ]
    add_documents(test_docs)
    save_index()
    print(f"Backend: {get_backend_name()}")
    print(f"Total vectors: {get_total_count()}")
    query_vec = np.random.randn(VECTOR_DIM).tolist()
    for r in search_similar(query_vec, "유도등", k=2):
        print(f"  Score: {r['score']:.4f} | Source: {r['source']} | Content: {r['content'][:50]}")
    print("✅ Vector Store Test Complete")
