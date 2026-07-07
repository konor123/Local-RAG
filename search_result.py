# -*- coding: utf-8 -*-
"""Normalized internal search result contract.

Public tool APIs still return their historical dictionaries. This module gives
new hybrid ranking code a stable internal shape for filename, vector, and future
FTS results.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SearchResult:
    source_path: str
    display_name: str
    snippet: str = ""
    score: float = 0.0
    source_engine: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunk_id: Optional[str] = None

    @property
    def identity(self) -> str:
        if self.chunk_id is not None:
            return f"{self.source_engine}:{self.chunk_id}"
        return os.path.normcase(os.path.abspath(self.source_path or self.display_name))

    def to_file_dict(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "path": self.source_path,
            "score": self.score,
            "source_engine": self.source_engine,
            "metadata": self.metadata,
        }

    def to_content_dict(self) -> Dict[str, Any]:
        return {
            "content": self.snippet,
            "source": self.source_path,
            "metadata": self.metadata,
            "score": self.score,
            "source_engine": self.source_engine,
        }


def from_file_result(item: Dict[str, Any], score: float = 0.0) -> SearchResult:
    path = item.get("path") or ""
    return SearchResult(
        source_path=path,
        display_name=item.get("name") or os.path.basename(path),
        snippet=item.get("snippet", ""),
        score=float(item.get("score", score) or 0.0),
        source_engine=item.get("source_engine", "filename"),
        metadata={k: v for k, v in item.items() if k not in {"name", "path", "snippet", "score", "source_engine"}},
    )


def from_content_result(item: Dict[str, Any], score: float = 0.0) -> SearchResult:
    path = item.get("source") or item.get("path") or ""
    metadata = dict(item.get("metadata") or {})
    chunk_id = item.get("chunk_id") or metadata.get("chunk_id")
    return SearchResult(
        source_path=path,
        display_name=item.get("name") or os.path.basename(path),
        snippet=item.get("content") or item.get("snippet") or "",
        score=float(item.get("score", score) or 0.0),
        source_engine=item.get("source_engine", "vector"),
        metadata=metadata,
        chunk_id=str(chunk_id) if chunk_id is not None else None,
    )
