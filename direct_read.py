"""Query-time direct file reading fallback.

This module extracts text from already-matched candidate files without
requiring embeddings. It deliberately does not OCR by default; scanned PDFs are
reported as requiring OCR so the query path stays bounded and responsive.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Dict, List, Optional


DEFAULT_MAX_CHARS = 50_000
DEFAULT_TIMEOUT_SECONDS = 10
PDF_OCR_MIN_CHARS = 50


DIRECT_READABLE_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".xls", ".hwp", ".hwpx", ".txt", ".html", ".htm"
}


def _error(path: str, category: str, detail: str, *, ocr_needed: bool = False) -> Dict:
    return {
        "success": False,
        "path": path,
        "source": path,
        "content": "",
        "metadata": {},
        "category": category,
        "detail": str(detail or "")[:1000],
        "source_engine": "direct_read",
        "ocr_needed": ocr_needed,
    }


def _join_loader_docs(docs: List[Dict], max_chars: int) -> tuple[str, Dict]:
    parts: List[str] = []
    metadata: Dict = {}
    remaining = max(0, int(max_chars))
    for item in docs:
        if not isinstance(item, dict):
            continue
        text = str(item.get("page_content") or "")
        if not text.strip():
            continue
        item_meta = item.get("metadata") or {}
        if isinstance(item_meta, dict) and not metadata:
            metadata = dict(item_meta)
        if remaining <= 0:
            break
        chunk = text[:remaining]
        parts.append(chunk)
        remaining -= len(chunk)
    content = "\n\n".join(parts).strip()
    if len(content) >= max_chars:
        content = content[:max_chars].rstrip() + "\n...(direct-read truncated)..."
    return content, metadata


def _load_file_content(path: str, max_chars: int) -> Dict:
    ext = os.path.splitext(path)[1].lower()
    if not path or not os.path.exists(path):
        return _error(path, "missing_file", "File does not exist")
    if ext not in DIRECT_READABLE_EXTENSIONS:
        return _error(path, "unsupported_extension", f"Unsupported extension: {ext}")

    try:
        from worker_loader import load_file

        loaded = load_file(path)
    except Exception as exc:
        return _error(path, "parse_error", str(exc))

    if isinstance(loaded, dict) and loaded.get("__loader_error__"):
        return _error(
            path,
            loaded.get("category", "parse_error"),
            loaded.get("detail", "Direct loader failed"),
            ocr_needed=ext == ".pdf",
        )
    if not isinstance(loaded, list):
        return _error(path, "parse_error", "Loader returned an unexpected result")

    content, metadata = _join_loader_docs(loaded, max_chars)
    if not content:
        return _error(path, "no_chunks", "Loader returned no text", ocr_needed=ext == ".pdf")
    ocr_needed = ext == ".pdf" and len(content.strip()) < PDF_OCR_MIN_CHARS
    return {
        "success": True,
        "path": path,
        "source": path,
        "content": content,
        "metadata": metadata,
        "category": "ok",
        "detail": "",
        "source_engine": "direct_read",
        "ocr_needed": ocr_needed,
    }


def load_file_content(path: str, max_chars: int = DEFAULT_MAX_CHARS, timeout_seconds: Optional[int] = DEFAULT_TIMEOUT_SECONDS) -> Dict:
    """Load file text for query-time fallback with a soft wall-clock timeout."""
    timeout = DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else max(1, int(timeout_seconds))
    result_queue: "queue.Queue[Dict]" = queue.Queue(maxsize=1)

    def _target() -> None:
        try:
            result_queue.put(_load_file_content(path, max_chars), block=False)
        except Exception as exc:
            try:
                result_queue.put(_error(path, "unknown_error", str(exc)), block=False)
            except Exception:
                pass

    # The daemon thread is intentionally orphaned on timeout: we return an error
    # to the caller immediately rather than blocking.  The thread will be cleaned
    # up by the Python runtime when it finishes or at interpreter exit.  This is
    # bounded because direct_read_candidates limits iteration to max_files (default 5)
    # and respects a total wall-clock timeout, so at most a handful of orphaned
    # loader threads can be in-flight at any time.
    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    try:
        return result_queue.get(timeout=timeout)
    except queue.Empty:
        return _error(path, "timeout", f"Direct file read exceeded {timeout}s")


def direct_read_candidates(file_results: List[Dict], config: Optional[Dict] = None) -> List[Dict]:
    """Direct-read top file-search candidates within count and time limits."""
    cfg = config or {}
    max_files = int(cfg.get("max_direct_read_files", 5) or 5)
    max_chars = int(cfg.get("max_direct_read_chars_per_file", DEFAULT_MAX_CHARS) or DEFAULT_MAX_CHARS)
    per_file_timeout = int(cfg.get("direct_read_file_timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
    total_timeout = int(cfg.get("direct_read_total_timeout_seconds", 30) or 30)
    started = time.monotonic()
    results: List[Dict] = []

    for item in (file_results or [])[:max_files]:
        path = item.get("path") or item.get("source")
        if not path:
            continue
        if time.monotonic() - started >= total_timeout:
            results.append(_error(path, "timeout", f"Direct-read total timeout exceeded {total_timeout}s"))
            break
        result = load_file_content(path, max_chars=max_chars, timeout_seconds=per_file_timeout)
        result["name"] = item.get("name") or os.path.basename(path)
        result["score"] = item.get("score")
        results.append(result)
    return results
