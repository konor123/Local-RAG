# -*- coding: utf-8 -*-
"""Hybrid search over existing filename and vector search paths.

This first iteration does not introduce user-facing search operators or SQLite
FTS. It only normalizes current results and fuses them with RRF.
"""
from __future__ import annotations

import re
import threading
from typing import Dict, List

from rrf import reciprocal_rank_fusion
from search_result import SearchResult, from_content_result, from_file_result


def normalize_file_results(result: Dict, limit: int = 10) -> List[SearchResult]:
    normalized = []
    for rank, item in enumerate((result or {}).get("results", [])[:limit], start=1):
        normalized.append(from_file_result(item, score=1.0 / rank))
    return normalized


def normalize_content_results(result: Dict, limit: int = 10) -> List[SearchResult]:
    normalized = []
    for rank, item in enumerate((result or {}).get("results", [])[:limit], start=1):
        normalized.append(from_content_result(item, score=float(item.get("score", 1.0 / rank) or 0.0)))
    return normalized


_QUERY_STOPWORDS = {"찾아줘", "찾아", "주세요", "파일", "내용", "요약", "요약해줘", "확인", "확인해줘", "관련", "문서", "검색"}


def _query_patterns(query: str) -> List[str]:
    if any(ch in query for ch in "*?"):
        return [query]
    tokens = [t for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", query or "") if t not in _QUERY_STOPWORDS]
    patterns: List[str] = []
    if tokens:
        patterns.append("*" + "*".join(tokens[:5]) + "*")
        for token in tokens[:2]:
            patterns.append(f"*{token}*")
    elif query.strip():
        patterns.append("*" + query.strip()[:30] + "*")
    return list(dict.fromkeys(patterns)) or ["*"]


def _search_content_with_timeout(query: str, k: int, timeout: int = 60) -> Dict:
    from tools import search_content

    result_box: Dict = {}

    def _run():
        try:
            result_box["result"] = search_content(query, k=k)
        except Exception as e:
            result_box["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        return {"error": f"검색 시간 초과({timeout}초)", "query": query, "count": 0, "results": [], "sources": []}
    if "error" in result_box:
        return {"error": result_box["error"], "query": query, "count": 0, "results": [], "sources": []}
    return result_box.get("result", {"query": query, "count": 0, "results": [], "sources": []})


def hybrid_search(query: str, k: int = 5, file_limit: int = 20) -> Dict:
    from tools import search_files

    merged_file_items = []
    seen_paths = set()
    component_file_count = 0
    for pattern in _query_patterns(query):
        file_result = search_files(pattern, sort_by="date_newest")
        component_file_count += file_result.get("count", 0) if isinstance(file_result, dict) else 0
        for item in (file_result or {}).get("results", []):
            path = item.get("path")
            if path and path not in seen_paths:
                merged_file_items.append(item)
                seen_paths.add(path)
            if len(merged_file_items) >= file_limit:
                break
        if len(merged_file_items) >= file_limit:
            break
    file_result = {"count": len(merged_file_items), "results": merged_file_items}
    content_result = _search_content_with_timeout(query, k=k)

    file_results = normalize_file_results(file_result, limit=file_limit)
    content_results = normalize_content_results(content_result, limit=max(k, file_limit))
    fused = reciprocal_rank_fusion([file_results, content_results], limit=k)

    sources = []
    seen = set()
    for item in fused:
        if item.source_path and item.source_path not in seen:
            sources.append(item.source_path)
            seen.add(item.source_path)

    return {
        "query": query,
        "count": len(fused),
        "results": [item.to_content_dict() for item in fused],
        "sources": sources,
        "source": "hybrid_rrf",
        "components": {
            "file_count": component_file_count,
            "content_count": content_result.get("count", 0) if isinstance(content_result, dict) else 0,
        },
    }
