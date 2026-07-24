# -*- coding: utf-8 -*-
"""Deterministic complexity routing for the unified RAG orchestrator."""
from __future__ import annotations

import re
from typing import Literal


ComplexityTier = Literal["factual", "complex", "creative"]

_FILE_LOOKUP_RE = re.compile(r"(파일|폴더|경로|드라이브|\.pdf\b|\.docx\b|\.xlsx\b)", re.IGNORECASE)
_MODEL_CODE_RE = re.compile(r"\b[A-Za-z]{2,}[A-Za-z0-9-]*\d[A-Za-z0-9-]*\b")

_FACTUAL_TERMS = {
    "가격", "단가", "전압", "정격", "치수", "크기", "규격", "사양", "재질",
    "수량", "납기", "유효기간", "기간", "인증", "담당자", "연락처", "설치",
}
_COMPLEX_TERMS = {
    "비교", "차이", "공통점", "모두", "전체", "각각", "합계", "총합", "평균",
    "목록", "리스트", "요약", "정리", "검증", "vs",
}
_CREATIVE_TERMS = {"예측", "전망", "추천", "개선안", "제안", "방안"}


def classify_complexity(question: str | None, history_str: str = "") -> ComplexityTier:
    """Classify only high-confidence factual questions for the fast path.

    Ambiguous, file-oriented, and multi-step requests deliberately default to
    ``complex`` so the existing LLM planner remains the safety net.
    """
    text = str(question or "").strip()
    if not text:
        return "complex"
    lower = text.lower()

    if history_str and any(indicator in text for indicator in (
        "그 제품", "이 제품", "저 제품", "그 문서", "이 문서", "저 문서",
        "그 파일", "이 파일", "해당",
    )):
        return "complex"

    if any(term in lower for term in _CREATIVE_TERMS):
        return "creative"
    if any(term in lower for term in _COMPLEX_TERMS):
        return "complex"
    if _FILE_LOOKUP_RE.search(text):
        return "complex"

    factual_signal_count = sum(term in lower for term in _FACTUAL_TERMS)
    has_model_code = bool(_MODEL_CODE_RE.search(text))
    if factual_signal_count and (has_model_code or len(text) >= 8):
        return "factual"
    return "complex"
