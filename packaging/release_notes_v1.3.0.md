## v1.3.0

### 주요 변경 사항

- Docufinder 분석을 바탕으로 검색 아키텍처 개선 1단계를 반영했습니다.
- 사용자-facing 검색 연산자는 추가하지 않았습니다.
- 내부 검색 결과 표준화 계약(`SearchResult`)을 추가했습니다.
- 기존 파일명 검색과 벡터 내용 검색을 RRF(Reciprocal Rank Fusion)로 병합하는 하이브리드 검색 경로를 추가했습니다.
- 하이브리드 검색은 자연어 질의를 파일명 검색용 토큰 패턴으로 변환하여 기존의 `*전체 문장*` 검색 실패를 줄입니다.
- 하이브리드 경로의 내용 검색에는 timeout 보호를 적용했습니다.
- 명확한 통합 검색 의도는 `query_router`를 통해 하이브리드 검색으로 분기합니다.
- `ARCHITECTURE.md`에 현재 구조와 향후 SQLite FTS5 sidecar 로드맵을 기록했습니다.

### 검증

- `python -m unittest tests.test_resource_guardrails tests.test_hybrid_search` 통과 (13/13)
- `python -m py_compile search_result.py rrf.py hybrid_search.py tools.py unified_engine.py query_router.py tests/test_hybrid_search.py tests/test_resource_guardrails.py` 통과
- Python sqlite3 FTS5 스파이크 통과
- reviewer 재검토: 블로커 없음

### 빌드 정보

- installer size: 888.8 MB
- SHA256: 6C7F9802616619B3770091F7DB817678442F87DE1B1A5C1FF848AF44D65A7690
