## v1.4.3

### 직접 파일 열람 fallback
- 임베딩/JIT 학습이 실패하거나 벡터 인덱스가 비어 있어도, 검색된 후보 파일을 직접 열어서 내용을 확인하고 답변할 수 있도록 변경했습니다.
- `direct_read.py`를 추가하여 `worker_loader.load_file()` 기반으로 임베딩 없이 텍스트를 추출합니다.
- PDF, DOCX, XLSX, HWP, TXT, HTML 등 기존 지원 확장자를 직접 열람 대상으로 포함합니다.
- PDF 텍스트 추출이 불가능한 경우 `ocr_needed` 플래그를 표시하고, OCR은 자동 실행하지 않습니다.

### JIT 실패 원인 상세 표시
- `BackgroundEmbedder.process_single_file_synchronous()`가 `True/False` 대신 `{success, category, detail, path}` 구조체를 반환하도록 변경했습니다.
- JIT 학습 실패 시 UI에 `(no_chunks): Loader returned no chunks` 같은 원인이 표시됩니다.
- JIT 실패 이벤트 타입을 `thinking`에서 `error`로 변경하여 빨간색으로 표시됩니다.

### 답변 근거 개선
- 파일 검색 결과가 있을 때 직접 열람한 텍스트를 LLM 답변 컨텍스트에 포함합니다.
- 직접 열람한 결과는 `source_engine: "direct_read"`로 출처가 표시됩니다.
- 파일당 3,000자, 전체 12,000자 제한으로 LLM 컨텍스트 초과를 방지합니다.

### 제한값
- 기본 제한: 파일 5개, 파일당 50,000자, 파일당 타임아웃 10초, 전체 타임아웃 30초
- 직접 열람 활성화/비활성화는 `search.enable_query_time_direct_read_fallback` 설정으로 제어

### 테스트
- 직접 파일 열람, loader 오류, PDF OCR 필요, 미지원 확장자, 전체 타임아웃 동작에 대한 단위 테스트를 추가했습니다.
- JIT 실패 시 category/detail 표시, 직접 열람 결과 연결, 답변 근거 포함에 대한 테스트를 추가했습니다.
- 기존 SQLite 테스트의 환경 의존 문제를 수정했습니다.
