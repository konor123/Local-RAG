## v1.4.5

### 적응형 메모리 기반 임베딩
- VectorStore 로드 전 metadata/index 파일 크기와 현재 사용 가능한 RAM을 함께 계산해 안전할 때만 eager load를 진행합니다.
- JSONL metadata의 Python 메모리 확장 비용, FAISS index 비용, 임베딩/일시 작업 reserve, 시스템 여유분을 반영해 판단합니다.
- Ollama가 이미 사용 중인 메모리는 사용 가능 RAM에 반영되므로 기본 reserve에서 중복 계산하지 않습니다.

### 메모리 부족 시 안전 대기
- 메모리 부족은 더 이상 VectorStore 오류나 세션 비활성화로 처리하지 않습니다.
- `waiting_for_memory` 상태에서 1분 → 5분 → 15분 간격으로 자동 재시도합니다.
- 메모리가 확보되면 앱 재시작 없이 자동으로 임베딩을 재개합니다.
- 메모리 대기는 error count 및 연속 로드 실패 횟수에 포함되지 않습니다.

### 인덱싱 상태 진단 강화
- 인덱싱 상태 화면에 사용 가능 RAM, 시스템 여유분, 향후 작업 reserve, 안전 store 예산, 예상 store 필요량, index/metadata 파일 크기, 메모리 대기 시간을 표시합니다.
- 트레이 상태에 `메모리 여유 대기 중`과 다음 재시도 시간을 표시합니다.

### 검증
- 8GB 여유 RAM에서 작은 store는 허용하고, 대형 metadata/index store는 대기하는 경계 테스트를 추가했습니다.
- 12GB 여유 RAM에서는 현재 보고된 대형 store를 허용하는 테스트를 추가했습니다.
- 전체 유지보수 테스트 스위트: 총 49개 중 44개 통과, 환경 의존 skip 5개.
