## v1.2.4

### 수정 사항

- 백그라운드 작업 중 순간적으로 CMD/PowerShell 창이 깜빡이는 문제를 완화했습니다.
- 네트워크 드라이브 감지(`net use`) 호출에 Windows 숨김 실행 플래그를 적용했습니다.
- 파일 검색(`findstr`) 호출에 Windows 숨김 실행 플래그를 적용했습니다.
- 업데이트 설치 실행 시 콘솔 창이 뜨지 않도록 조정했습니다.
- standalone ingest 경로의 PowerShell, LibreOffice, worker subprocess 호출에도 동일한 숨김 실행 플래그를 적용했습니다.

### 수정 사항 (추가)

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
- subprocess call-site audit: 7/7 관련 호출 보호 확인
- `python -m unittest tests.test_resource_guardrails` 통과 (6/6)
- reviewer 재검토: 블로커 없음, ship 가능
- installer size: 888.8 MB
- SHA256: 4BF7FC82BC8B3B66566E9F575CC6397AC8BDCE087FE6B9917333FEE8370B9FD5
