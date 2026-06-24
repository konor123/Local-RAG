# -*- coding: utf-8 -*-
"""
Query Router - 쿼리 의도 분류기
사용자 쿼리를 분석하여 적절한 검색 방식을 결정합니다.
"""
import re
from typing import Literal

QueryIntent = Literal["file_search", "content_search", "hybrid"]


# 파일 검색 의도 패턴 (파일명/위치 찾기)
FILE_SEARCH_PATTERNS = [
    # 파일/폴더 찾기
    r"파일\s*(찾|검색|있|어디)",
    r"폴더\s*(찾|검색|있|어디)",
    r"(찾아|검색해|보여)\s*(줘|줘요|주세요)",
    r"있(어|나요|는지|을까)",
    r"어디\s*(에|있|야)",
    r"경로\s*(알려|찾)",
    # 드라이브 탐색
    r"[XYZW]:\s*[드라이브]?\s*(에|의|내|안)",
    r"드라이브\s*(에|뭐|무엇|있)",
    # 파일명 패턴
    r"\*[\w가-힣]+\*",  # 와일드카드 패턴
    r"\.(pdf|xlsx|docx|dwg|pptx)",  # 확장자 언급
    # 목록/리스트 요청
    r"(목록|리스트|list)\s*(보여|알려|줘)",
]

# 내용 검색 의도 패턴 (문서 내용/의미 검색)
CONTENT_SEARCH_PATTERNS = [
    # 내용/정보 질문
    r"(뭐야|뭔가요|무엇)",
    r"(알려|설명|요약)(줘|해)",
    r"(어떻게|방법|절차|과정)",
    r"(스펙|사양|규격|spec)",
    r"(정의|개념|의미)",
    # 내용 기반 질문
    r"(내용|텍스트|문서)\s*(에|에서|중)",
    r"(에\s*대해|관련\s*정보)",
    r"(작동|동작|원리|기능)",
    r"(차이|비교|vs)",
    # 지식 질문
    r"(왜|이유|원인)",
    r"(언제|날짜|기간)",
    r"(누가|담당자|연락처)",
]

# 하이브리드 의도 패턴 (파일 찾기 + 내용 읽기)
HYBRID_PATTERNS = [
    r"(파일|문서).*(내용|읽|확인|열)",
    r"(내용|안에).*(뭐|무엇).*있",
    r"(찾|검색).*(읽|확인|요약)",
    r"(카탈로그|견적서|자료).*(보|확인|내용)",
    r"(어디|찾).*(알려|설명)",
]


def classify_query(query: str) -> QueryIntent:
    """
    쿼리의 의도를 분류합니다.
    
    Args:
        query: 사용자 쿼리
    
    Returns:
        "file_search" - 파일명/위치 검색 (Agent)
        "content_search" - 내용/의미 검색 (RAG)
        "hybrid" - 파일 검색 + 내용 읽기
    """
    query_lower = query.lower()
    
    # 점수 계산
    scores = {
        "file_search": 0,
        "content_search": 0,
        "hybrid": 0
    }
    
    # 하이브리드 패턴 체크 (먼저 검사, 높은 우선순위)
    for pattern in HYBRID_PATTERNS:
        if re.search(pattern, query):
            scores["hybrid"] += 3
    
    # 파일 검색 패턴 체크
    for pattern in FILE_SEARCH_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            scores["file_search"] += 2
    
    # 내용 검색 패턴 체크
    for pattern in CONTENT_SEARCH_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            scores["content_search"] += 2
    
    # 특정 키워드 보너스
    if any(kw in query for kw in ["파일", "폴더", "드라이브", "경로"]):
        scores["file_search"] += 1
    
    if any(kw in query for kw in ["설명", "알려줘", "뭐야", "어떻게"]):
        scores["content_search"] += 1
    
    # 확장자 언급은 파일 검색 의도
    if re.search(r"\.(pdf|xlsx|docx|dwg|pptx|xls|doc)", query_lower):
        scores["file_search"] += 2
    
    # 최고 점수 의도 선택
    max_intent = max(scores, key=scores.get)
    
    # 점수가 모두 0이면 기본값은 content_search (RAG가 더 범용적)
    if scores[max_intent] == 0:
        return "content_search"
    
    # hybrid와 다른 의도가 동점이면 hybrid 우선
    if scores["hybrid"] > 0 and scores["hybrid"] >= max(scores["file_search"], scores["content_search"]):
        return "hybrid"
    
    return max_intent


def get_intent_description(intent: QueryIntent) -> str:
    """의도에 대한 설명을 반환합니다."""
    descriptions = {
        "file_search": "🔍 파일 검색 (파일명 기반)",
        "content_search": "📚 내용 검색 (RAG 벡터 검색)",
        "hybrid": "🔄 통합 검색 (파일 찾기 + 내용 분석)"
    }
    return descriptions.get(intent, "Unknown")


if __name__ == "__main__":
    # 테스트
    test_queries = [
        # 파일 검색
        ("불꽃감지기 카탈로그 파일 찾아줘", "file_search"),
        ("X드라이브에 견적서 있어?", "file_search"),
        ("*.pdf 파일 검색해줘", "file_search"),
        
        # 내용 검색
        ("불꽃감지기 설치 방법 알려줘", "content_search"),
        ("OSL-FD-IR3X 스펙이 뭐야?", "content_search"),
        ("2026년 매출 목표는?", "content_search"),
        
        # 하이브리드
        ("유도등 카탈로그 내용 요약해줘", "hybrid"),
        ("견적서 파일 찾아서 내용 확인해줘", "hybrid"),
    ]
    
    print("Query Router 테스트")
    print("=" * 60)
    
    correct = 0
    for query, expected in test_queries:
        result = classify_query(query)
        status = "✅" if result == expected else "❌"
        if result == expected:
            correct += 1
        print(f"{status} '{query}'")
        print(f"   예상: {expected}, 결과: {result}")
    
    print("=" * 60)
    print(f"정확도: {correct}/{len(test_queries)} ({100*correct/len(test_queries):.0f}%)")
