# -*- coding: utf-8 -*-
"""Reciprocal Rank Fusion helpers for hybrid retrieval."""
from __future__ import annotations

from typing import Iterable, List

from search_result import SearchResult


def reciprocal_rank_fusion(result_lists: Iterable[List[SearchResult]], k: int = 60, limit: int = 10) -> List[SearchResult]:
    """Merge ranked result lists with Reciprocal Rank Fusion.

    Each input list is already ranked best-first. Scores are replaced with the
    fused RRF score so downstream callers can sort/render consistently.
    """
    by_id = {}
    scores = {}

    for results in result_lists:
        for rank, result in enumerate(results, start=1):
            identity = result.identity
            if identity not in by_id:
                by_id[identity] = result
                scores[identity] = 0.0
            scores[identity] += 1.0 / (k + rank)

    fused = []
    for identity, result in by_id.items():
        fused_result = SearchResult(
            source_path=result.source_path,
            display_name=result.display_name,
            snippet=result.snippet,
            score=scores[identity],
            source_engine=result.source_engine,
            metadata={**result.metadata, "rrf_score": scores[identity]},
            chunk_id=result.chunk_id,
        )
        fused.append(fused_result)

    fused.sort(key=lambda item: (-item.score, item.display_name.lower(), item.source_path.lower()))
    return fused[:limit]
