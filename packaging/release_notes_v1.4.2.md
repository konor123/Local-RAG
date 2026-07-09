## v1.4.2

### UI 다크모드 대응
- 참고파일 카드와 코드블록의 `#ffffff` 하드코딩을 제거하고 `background:transparent`로 변경했습니다.
- 참고파일 카드의 텍스트색상이 다크모드에서도 읽히도록 밝은 색상(`#cbd5e1`, `#94a3b8`)으로 개선했습니다.
- 코드블록 스크롤바와 헤더 텍스트도 다크모드 대응 색상으로 변경했습니다.

### 검색 안정성 강화
- LLM Planner가 tools 모드에서 file 검색을 누락하는 경우를 대비해 generic file-search safety net을 추가했습니다.
- `_ensure_file_search_safety_net()` 함수로 `build_glob_patterns()` 기반 보강 로직을 적용했습니다.
- 하드코딩된 검색어 접미사 없이 일반적인 파일 검색 패턴만 생성하도록 설계했습니다.

### 테스트
- 다크모드 대응에 대한 assertion을 업데이트했습니다.
- file-search safety net 동작에 대한 단위 테스트를 추가했습니다.
- 하이브리드 도구 선택 및 LLM 재검색 관련 기대값을 정비했습니다.
