## v1.2.5

### 수정 사항

- 백그라운드 임베딩 리소스 폭주 방지 로직을 추가했습니다.
  - FAISS/TurboVec 인덱스/metadata 로드 전 크기 guardrail을 추가했습니다.
  - VectorStore 로드 실패 시 backoff 및 세션 중지 로직을 추가했습니다.
  - `.dwg` 파일을 실제 임베딩 대상에 추가했습니다.
  - VectorStore 로드 전에 처리 가능한 파일만 사전 필터링하도록 개선했습니다.
- 휴지통(`$Recycle.Bin`)은 기본 포함 설정을 유지합니다.
- SQLite metadata, sharding 등 인덱스 구조 개선은 다음 버전으로 연기합니다.

### 검증

- Python compileall 통과
- import smoke test 통과
- `python -m unittest tests.test_resource_guardrails` 통과 (6/6)
- reviewer 재검토: 블로커 없음, ship 가능
- installer size: 888.8 MB
- SHA256: 75ECFFBDE636CA5E0EFD9818EBDAC50F8F56B4C22D5FC25FFB81ED8217276376
