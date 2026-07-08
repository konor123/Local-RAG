# -*- coding: utf-8 -*-
"""Shared search-term extraction helpers.

Keep filename/hybrid/manual fallback keyword extraction consistent so Korean
helper words such as "최신" or "찾아줘" do not leak into glob patterns.
"""
from __future__ import annotations

import re
from typing import List


SEARCH_STOPWORDS = {
    "찾아줘",
    "찾아",
    "주세요",
    "최신",
    "최근",
    "파일",
    "자료",
    "관련",
    "대한",
    "있는",
    "알려줘",
    "내용",
    "요약",
    "요약해줘",
    "확인",
    "확인해줘",
    "문서",
    "검색",
}


def extract_search_tokens(text: str, limit: int = 5) -> List[str]:
    """Return significant alphanumeric/Korean tokens from a user query."""
    tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", text or "")
    return [token for token in tokens if token not in SEARCH_STOPWORDS][:limit]


def build_glob_patterns(
    query: str,
    *,
    token_limit: int = 5,
    single_token_limit: int = 2,
    max_patterns: int | None = None,
    preserve_existing_glob: bool = False,
) -> List[str]:
    """Build deduplicated filename glob patterns from significant query tokens."""
    if preserve_existing_glob and any(ch in (query or "") for ch in "*?"):
        return [query]

    tokens = extract_search_tokens(query, limit=token_limit)
    patterns: List[str] = []
    if tokens:
        patterns.append("*" + "*".join(tokens) + "*")
        for token in tokens[:single_token_limit]:
            patterns.append(f"*{token}*")
    elif (query or "").strip():
        patterns.append("*" + query.strip()[:30] + "*")

    deduped = list(dict.fromkeys(patterns)) or ["*"]
    if max_patterns is not None:
        return deduped[:max_patterns]
    return deduped
