# -*- coding: utf-8 -*-
"""
Background Embedder - 저자원 백그라운드 임베딩
적은 자원으로 천천히 임베딩을 진행합니다.
"""
import os
import sys
import json
import time
import threading
import queue
import logging
from typing import List, Set, Optional, Callable
from datetime import datetime
from collections import defaultdict, Counter
from config_manager import load_config
from runtime_paths import runtime_path

# 환경 변수 설정 (저자원 모드)
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "2"  # 최소 스레드
os.environ["ANONYMIZED_TELEMETRY"] = "False"

PROCESSED_FILE = os.environ.get("PROCESSED_FILES_PATH", runtime_path("processed_files.txt"))
VECTOR_STORE_PATH = os.environ.get("VECTOR_STORE_PATH", runtime_path("chroma_db_ko"))
LOG_FILE = os.environ.get("EMBED_LOG_PATH", runtime_path("logs", "embed_log.txt"))

# 임베딩 가능한 확장자
EMBEDDABLE_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".pptx", ".ppt", ".dwg", ".hwp", ".hwpx", ".txt", ".html", ".htm"
}

PRIORITY_EXTS = {
    ".xlsx": 0, ".xls": 0,
    ".docx": 1, ".doc": 1,
    ".hwp": 1, ".hwpx": 1, ".txt": 1,
    ".pptx": 2, ".ppt": 2,
    ".pdf": 3,
    ".dwg": 99,
}

def _priority(file_path: str) -> int:
    return PRIORITY_EXTS.get(os.path.splitext(file_path)[1].lower(), 10)

def _classify_error_text(text: str) -> str:
    lower = (text or "").lower()
    if "xlrd" in lower or "missing optional dependency" in lower:
        return "missing_dependency"
    if "unicode" in lower or "codec" in lower or "decode" in lower or "encoding" in lower:
        return "decode_error"
    if "timeout" in lower or "timed out" in lower:
        return "timeout"
    if "winerror" in lower or "network" in lower or "경로" in lower or "semaphore" in lower:
        return "network_error"
    if "encrypted" in lower or "password" in lower:
        return "empty_or_encrypted"
    if "unsupported" in lower:
        return "unsupported_extension"
    if lower.strip():
        return "parse_error"
    return "unknown_error"

def _is_temporary_office_file(file_path: str) -> bool:
    return os.path.basename(file_path).startswith("~$")


def _embedding_config() -> dict:
    return load_config().get("embedding", {})


def _positive_int(value, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return result if result > 0 else default


def _backoff_seconds(config: dict) -> List[int]:
    values = config.get("retry_backoff_seconds", [60, 300, 900, 3600])
    if not isinstance(values, list) or not values:
        return [60, 300, 900, 3600]
    parsed = []
    for value in values:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            parsed.append(seconds)
    return parsed or [60, 300, 900, 3600]

# 파일 로거 설정
def _get_logger():
    logger = logging.getLogger("embedder")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        try:
            fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(fh)
        except Exception:
            logger.addHandler(logging.NullHandler())
    return logger

_log = _get_logger()


class BackgroundEmbedder:
    """저자원 백그라운드 임베딩 처리기"""
    
    def __init__(
        self,
        sleep_between_files: float = 5.0,  # 파일 간 대기 시간 (초)
        idle_sleep: float = 60.0,  # 처리할 파일이 없을 때 재확인 대기 시간
        batch_size: int = 10,  # 배치 크기
        chunk_size: int = 500,  # 텍스트 청크 크기
        chunk_overlap: int = 50  # 청크 오버랩
    ):
        self.sleep_between_files = sleep_between_files
        self.idle_sleep = idle_sleep
        self.batch_size = batch_size
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        self._model = None  # 지연 로딩
        self._vectorstore = None
        self._is_running = False
        self._should_stop = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        
        # 상태 추적
        self._processed_count = 0
        self._skip_count = 0
        self._error_count = 0
        self._current_file = ""
        self._last_error = ""
        self._last_error_category = ""
        self._state = "disabled" if not bool(_embedding_config().get("enabled", True)) else "idle"
        self._source_total = 0
        self._processable_total = 0
        self._current_index = 0
        self._remaining_count = 0
        self._last_status_updated_at = time.time()
        self._status_callback: Optional[Callable] = None
        self._file_provider: Optional[Callable[[], List[str]]] = None
        self._limit: Optional[int] = None
        self._extension_stats = defaultdict(Counter)
        self._config = _embedding_config()
        self._embedding_enabled = bool(self._config.get("enabled", True))
        self._max_load_failures = _positive_int(self._config.get("max_load_failures"), 3)
        self._retry_backoff_seconds = _backoff_seconds(self._config)
        self._max_file_size_bytes = _positive_int(self._config.get("max_file_size_mb"), 200) * 1024 * 1024
        self._consecutive_load_failures = 0
        self._backoff_until = 0.0
        self._embedding_disabled_for_session = not self._embedding_enabled
        self._last_vectorstore_error = ""
        self._memory_wait_until = 0.0
        self._memory_wait_attempts = 0
        self._memory_wait_details = {}
        
        # 처리된 파일 목록 로드
        self._processed_files: Set[str] = self._load_processed_files()

    def _update_state(
        self,
        state: Optional[str] = None,
        current_file: Optional[str] = None,
        source_total: Optional[int] = None,
        processable_total: Optional[int] = None,
        current_index: Optional[int] = None,
        remaining_count: Optional[int] = None,
        last_error: Optional[str] = None,
    ) -> None:
        """Update coarse progress fields for UI polling.

        Existing counters remain cumulative. These fields describe the current
        queue snapshot so idle monitoring is not rendered as fake 0/0/0 work.
        """
        with self._lock:
            if state is not None:
                self._state = state
            if current_file is not None:
                self._current_file = current_file
            if source_total is not None:
                self._source_total = max(0, int(source_total))
            if processable_total is not None:
                self._processable_total = max(0, int(processable_total))
            if current_index is not None:
                self._current_index = max(0, int(current_index))
            if remaining_count is not None:
                self._remaining_count = max(0, int(remaining_count))
            if last_error is not None:
                self._last_error = last_error
            self._last_status_updated_at = time.time()

    def _record_sidecar_status(self, filepath: str, status: str, detail: str = "") -> None:
        try:
            from sqlite_index import safe_record_status

            safe_record_status(filepath, status, detail)
        except Exception:
            pass

    def _record_sidecar_chunks(self, filepath: str, chunks: List[dict]) -> None:
        try:
            from sqlite_index import safe_upsert_chunks

            safe_upsert_chunks(filepath, chunks, status="ok")
        except Exception:
            pass

    def _record_status(self, filepath: str, status: str) -> None:
        ext = os.path.splitext(filepath)[1].lower() or "<no_ext>"
        with self._lock:
            self._extension_stats[ext][status] += 1
            self._extension_stats["__total__"][status] += 1

    def _set_last_failure(self, category: str, detail: str) -> None:
        self._last_error_category = category or "unknown_error"
        self._last_error = str(detail or "")[:1000]

    def _jit_result(self, filepath: str, success: bool, category: str = "", detail: str = "") -> dict:
        return {
            "success": bool(success),
            "path": filepath,
            "category": category or ("ok" if success else self._last_error_category or "unknown_error"),
            "detail": detail if detail else ("" if success else self._last_error),
        }
    
    def _load_processed_files(self) -> Set[str]:
        """처리된 파일 목록 로드"""
        if not os.path.exists(PROCESSED_FILE):
            return set()
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return set(line.strip() for line in f)
        except Exception:
            return set()
    
    def _save_processed_file(self, filepath: str) -> None:
        """처리된 파일 추가"""
        try:
            with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
                f.write(f"{filepath}\n")
            self._processed_files.add(filepath)
        except Exception as e:
            print(f"[Embedder] Error saving processed file: {e}")

    def _is_processable_file(self, filepath: str) -> bool:
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in EMBEDDABLE_EXTENSIONS:
            return False
        if _is_temporary_office_file(filepath):
            return False
        try:
            if not os.path.exists(filepath):
                return False
            if os.path.getsize(filepath) > self._max_file_size_bytes:
                return False
        except OSError:
            return False
        return True

    def _filter_processable_files(self, files: List[str]) -> List[str]:
        return [f for f in files if self._is_processable_file(f)]

    def _current_backoff_seconds(self) -> int:
        index = max(0, min(self._consecutive_load_failures - 1, len(self._retry_backoff_seconds) - 1))
        return self._retry_backoff_seconds[index]

    def _register_vectorstore_load_success(self) -> None:
        with self._lock:
            self._consecutive_load_failures = 0
            self._backoff_until = 0.0
            self._last_vectorstore_error = ""
        self._clear_memory_wait()

    def _register_vectorstore_load_failure(self, error: str) -> int:
        with self._lock:
            self._consecutive_load_failures += 1
            self._last_vectorstore_error = error
            if self._consecutive_load_failures >= self._max_load_failures:
                self._embedding_disabled_for_session = True
                self._backoff_until = 0.0
                return 0
            delay = self._current_backoff_seconds()
            self._backoff_until = time.time() + delay
            return delay

    def _vectorstore_retry_delay(self) -> int:
        if self._embedding_disabled_for_session:
            return 0
        remaining = int(max(0, self._backoff_until - time.time()))
        return remaining

    def _memory_wait_retry_delay(self) -> int:
        return int(max(0, self._memory_wait_until - time.time()))

    def _register_memory_wait(self, diagnostics: dict) -> int:
        """Schedule a retryable memory wait without consuming failure budget."""
        with self._lock:
            self._memory_wait_attempts += 1
            delays = [60, 300, 900]
            delay = delays[min(self._memory_wait_attempts - 1, len(delays) - 1)]
            self._memory_wait_until = time.time() + delay
            self._memory_wait_details = dict(diagnostics or {})
            return delay

    def _clear_memory_wait(self) -> None:
        with self._lock:
            self._memory_wait_until = 0.0
            self._memory_wait_attempts = 0
            self._memory_wait_details = {}
    
    def _lazy_load_model(self) -> bool:
        """모델 지연 로딩"""
        if self._model is not None:
            return True
        
        try:
            print("[Embedder] Loading embedding model (this may take a moment)...")
            from langchain_community.embeddings import HuggingFaceEmbeddings
            
            self._model = HuggingFaceEmbeddings(
                model_name="dragonkue/multilingual-e5-small-ko",
                model_kwargs={'device': 'cpu'},
                encode_kwargs={'normalize_embeddings': True}
            )
            print("[Embedder] Model loaded successfully")
            return True
        except Exception as e:
            self._last_error = f"Model loading failed: {e}"
            print(f"[Embedder] {self._last_error}")
            return False
    
    def _lazy_load_vectorstore(self) -> bool:
        """벡터스토어 인덱스 지연 로딩"""
        if self._vectorstore is not None:
            return True
        if self._embedding_disabled_for_session:
            self._last_error = "Embedding disabled for this session after repeated VectorStore load failures"
            return False
        memory_wait_delay = self._memory_wait_retry_delay()
        if memory_wait_delay > 0:
            self._last_error = f"Waiting for memory for {memory_wait_delay}s"
            return False
        retry_delay = self._vectorstore_retry_delay()
        if retry_delay > 0:
            self._last_error = f"VectorStore load is in backoff for {retry_delay}s: {self._last_vectorstore_error}"
            return False
        
        try:
            from faiss_store import load_index, get_backend_name
            
            load_index()
            self._vectorstore = True  # 이름은 _vectorstore 유지 (호환)
            self._register_vectorstore_load_success()
            print(f"[Embedder] VectorStore index loaded successfully ({get_backend_name()})")
            return True
        except Exception as e:
            try:
                from faiss_store import MemoryPressureError
            except Exception:
                MemoryPressureError = ()
            if isinstance(e, MemoryPressureError):
                diagnostics = getattr(e, "diagnostics", {})
                delay = self._register_memory_wait(diagnostics)
                self._last_error = str(e)
                self._last_vectorstore_error = self._last_error
                print(f"[Embedder] Memory pressure; retrying after {delay}s: {self._last_error}")
                return False
            error = str(e)
            self._last_error = f"VectorStore load failed: {error}"
            self._register_vectorstore_load_failure(error)
            print(f"[Embedder] {self._last_error}")
            return False
    
    def _process_file(self, filepath: str) -> bool:
        """단일 파일 처리"""
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        
        self._update_state(current_file=os.path.basename(filepath))
        
        # 확장자 필터
        ext = os.path.splitext(filepath)[1].lower()
        if _is_temporary_office_file(filepath):
            self._skip_count += 1
            self._set_last_failure("temporary_file", "Office temporary lock file")
            self._record_status(filepath, "temporary_file")
            self._record_sidecar_status(filepath, "temporary_file", "Office temporary lock file")
            return False
        if ext not in EMBEDDABLE_EXTENSIONS:
            self._skip_count += 1
            self._set_last_failure("unsupported_extension", f"Unsupported extension: {ext}")
            self._record_status(filepath, "unsupported_extension")
            self._record_sidecar_status(filepath, "unsupported_extension", f"Unsupported extension: {ext}")
            return False  # 지원하지 않는 확장자 → 무시 (로그 안 남김)
        
        # 파일 존재 확인
        if not os.path.exists(filepath):
            _log.debug(f"SKIP (not found): {filepath}")
            self._skip_count += 1
            self._set_last_failure("missing_file", "File no longer exists")
            self._record_status(filepath, "missing_file")
            self._record_sidecar_status(filepath, "missing_file", "File no longer exists")
            return False
        
        try:
            # 파일 로드 (direct import — frozen env 호환)
            from worker_loader import load_file

            data = load_file(filepath)

            if isinstance(data, dict) and data.get("__loader_error__"):
                category = data.get("category", "parse_error")
                detail = data.get('detail', '')
                if category in ("unsupported_extension", "empty_or_encrypted", "temporary_file"):
                    self._skip_count += 1
                else:
                    self._error_count += 1
                self._set_last_failure(category, detail)
                self._record_status(filepath, category)
                self._record_sidecar_status(filepath, category, detail)
                _log.warning(f"FAIL ({category}): {self._current_file} | {detail[:100]}")
                return False

            # PDF OCR 보강
            if ext == ".pdf" and data:
                try:
                    from langchain_core.documents import Document
                    from ocr_utils import augment_pdf_documents_with_ocr

                    docs = [
                        Document(
                            page_content=item.get("page_content", ""),
                            metadata=item.get("metadata", {})
                        )
                        for item in data
                        if isinstance(item, dict) and "page_content" in item
                    ]
                    docs = augment_pdf_documents_with_ocr(docs, filepath)
                    data = [
                        {"page_content": doc.page_content, "metadata": doc.metadata}
                        for doc in docs
                    ]
                except Exception as ocr_error:
                    _log.warning(f"WARN (ocr augmentation failed): {self._current_file} | {str(ocr_error)[:100]}")
            if not data:
                _log.debug(f"SKIP (no chunks): {self._current_file}")
                self._skip_count += 1
                self._set_last_failure("no_chunks", "Loader returned no chunks")
                self._record_status(filepath, "no_chunks")
                self._record_sidecar_status(filepath, "no_chunks", "Loader returned no chunks")
                return False
            
            # VectorStore index 추가
            from faiss_store import add_documents, get_backend_name, save_index
            
            vector_docs = []
            for item in data:
                if isinstance(item, dict) and 'page_content' in item:
                    vector_docs.append({
                        "content": item['page_content'],
                        "source": item.get('metadata', {}).get('source', filepath),
                        "vector": None,
                        "metadata": item.get('metadata', {})
                    })
            
            if not vector_docs:
                _log.debug(f"SKIP (no valid docs): {self._current_file}")
                self._skip_count += 1
                self._set_last_failure("no_chunks", "No valid document chunks")
                self._record_status(filepath, "no_chunks")
                self._record_sidecar_status(filepath, "no_chunks", "No valid document chunks")
                return False
            
            # 임베딩 생성 (모델 로딩)
            if not self._lazy_load_model():
                _log.error(f"FAIL (model not loaded): {self._current_file}")
                self._error_count += 1
                self._set_last_failure("embedding_error", self._last_error)
                self._record_status(filepath, "embedding_error")
                self._record_sidecar_status(filepath, "embedding_error", self._last_error)
                return False
            
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap
            )
            
            # 청크 분할 + 임베딩
            final_docs = []
            for doc in vector_docs:
                chunks = splitter.split_text(doc['content'])
                for chunk in chunks:
                    vec = self._model.embed_query(chunk)
                    final_docs.append({
                        "content": chunk,
                        "source": doc['source'],
                        "vector": vec,
                        "metadata": doc['metadata']
                    })
            
            if final_docs:
                add_documents(final_docs)
                save_index()  # 매 파일마다 저장 (크래시 방지)
                if get_backend_name() != "turbovec":
                    self._record_sidecar_chunks(filepath, final_docs)
                self._processed_count += 1
                self._record_status(filepath, "ok")
                _log.info(f"OK ({len(final_docs)} chunks): {self._current_file}")
                return True
            else:
                _log.debug(f"SKIP (0 chunks after split): {self._current_file}")
                self._skip_count += 1
                self._set_last_failure("no_chunks", "Text splitter produced no chunks")
                self._record_status(filepath, "no_chunks")
                self._record_sidecar_status(filepath, "no_chunks", "Text splitter produced no chunks")
                return False
            
        except Exception as e:
            category = _classify_error_text(str(e))
            self._set_last_failure(category, f"Error: {self._current_file} - {str(e)[:100]}")
            _log.error(f"FAIL (exception): {self._current_file} | {str(e)[:150]}")
            self._error_count += 1
            self._record_status(filepath, category)
            self._record_sidecar_status(filepath, category, str(e))
            return False
    
    def _embedding_loop(self, files: List[str]) -> None:
        """임베딩 메인 루프.

        When a file provider is supplied, keep the worker alive and periodically
        re-check the latest cache so network-drive files discovered later are
        embedded without requiring a manual restart.
        """
        _log.info(f"=== Embedding loop START: {len(files)} initial files ===")
        print(f"[Embedder] Starting embedding loop for {len(files)} initial files")
        self._update_state(
            state="scanning",
            current_file="파일 확인 중",
            source_total=len(files),
            processable_total=0,
            current_index=0,
            remaining_count=0,
        )

        try:
            while not self._should_stop:
                source_files = files
                if self._file_provider is not None:
                    try:
                        source_files = self._file_provider() or []
                    except Exception as provider_error:
                        self._last_error = f"File provider failed: {provider_error}"
                        _log.warning(self._last_error)
                        self._update_state(state="error", last_error=self._last_error)
                        source_files = []

                new_files = sorted(
                    [f for f in source_files if f not in self._processed_files],
                    key=_priority,
                )
                if self._limit:
                    new_files = new_files[: self._limit]

                processable_files = self._filter_processable_files(new_files)
                self._update_state(
                    state="scanning",
                    source_total=len(source_files),
                    processable_total=len(processable_files),
                    current_index=0,
                    remaining_count=len(processable_files),
                )

                if not processable_files:
                    self._update_state(
                        state="idle",
                        current_file="모니터링 중",
                        processable_total=0,
                        current_index=0,
                        remaining_count=0,
                    )
                    if self._status_callback:
                        self._status_callback(self._processed_count, len(source_files), self._current_file, False)
                    if self._file_provider is None:
                        break
                    _log.debug(f"No processable new files; rechecking in {self.idle_sleep}s")
                    time.sleep(self.idle_sleep)
                    continue

                # 모델 및 벡터스토어 로드 (처리할 파일이 있을 때만)
                self._update_state(state="loading", current_file="인덱스 준비 중", remaining_count=len(processable_files))
                if not self._lazy_load_vectorstore():
                    if self._embedding_disabled_for_session:
                        self._update_state(state="disabled", current_file="임베딩 비활성화", last_error=self._last_error)
                        _log.error(f"VectorStore load FAILED: {self._last_error} — embedding disabled for this session")
                        break
                    memory_wait = self._memory_wait_retry_delay()
                    retry_delay = memory_wait or self._vectorstore_retry_delay() or self.idle_sleep
                    state = "waiting_for_memory" if memory_wait else "backoff"
                    current_file = "메모리 여유 대기 중" if memory_wait else f"재시도 대기 {retry_delay}s"
                    self._update_state(state=state, current_file=current_file, last_error=self._last_error)
                    if memory_wait:
                        _log.warning(f"VectorStore waiting for memory: {self._last_error} — retrying after {retry_delay}s")
                    else:
                        _log.error(f"VectorStore load FAILED: {self._last_error} — retrying after {retry_delay}s")
                    time.sleep(retry_delay)
                    continue
                _log.info("VectorStore loaded OK")

                for i, filepath in enumerate(processable_files):
                    if self._should_stop:
                        _log.info("Stop requested, exiting loop")
                        break

                    if filepath in self._processed_files:
                        continue

                    self._update_state(
                        state="embedding",
                        current_file=os.path.basename(filepath),
                        current_index=i + 1,
                        processable_total=len(processable_files),
                        remaining_count=max(0, len(processable_files) - (i + 1)),
                    )
                    success = self._process_file(filepath)

                    if success:
                        self._save_processed_file(filepath)

                    if self._status_callback:
                        self._status_callback(self._processed_count, len(processable_files), self._current_file, success)

                    checked = i + 1
                    if checked % 100 == 0:
                        _log.info(f"Progress: checked={checked}/{len(processable_files)} | ok={self._processed_count} | skip={self._skip_count} | err={self._error_count}")

                    if success and not self._should_stop:
                        time.sleep(self.sleep_between_files)

                if self._file_provider is None:
                    break
                self._update_state(state="idle", current_file="모니터링 중", current_index=0, remaining_count=0)
        finally:
            final_state = "stopped" if self._should_stop else ("idle" if self._file_provider is not None else "completed")
            with self._lock:
                self._is_running = False
            self._update_state(state=final_state, current_file="", current_index=0, remaining_count=0)
            _log.info(f"=== Embedding loop END: ok={self._processed_count} | skip={self._skip_count} | err={self._error_count} ===")
            print(f"[Embedder] Embedding loop stopped. Processed: {self._processed_count}")
    
    def start(self, files: List[str], status_callback: Callable = None, limit: int = None, file_provider: Callable[[], List[str]] = None) -> bool:
        """
        백그라운드 임베딩 시작
        
        Args:
            files: 처리할 파일 목록
            status_callback: 상태 콜백 (current, total, filename, success)
            limit: 테스트용 최대 처리 파일 수. None이면 제한 없음.
        
        Returns:
            시작 성공 여부
        """
        files = files or []
        with self._lock:
            if self._is_running:
                return False
            if self._embedding_disabled_for_session:
                self._update_state(state="disabled", current_file="임베딩 비활성화")
                return False

            # 새 파일만 필터링
            new_files = sorted([f for f in files if f not in self._processed_files], key=_priority)
            if limit:
                new_files = new_files[:limit]

            self._is_running = True
            self._should_stop = False
            self._status_callback = status_callback
            self._file_provider = file_provider
            self._limit = limit
            self._state = "scanning"
            self._source_total = len(files)
            self._processable_total = len(new_files)
            self._current_index = 0
            self._remaining_count = len(new_files)
            self._current_file = "파일 확인 중"
            self._last_status_updated_at = time.time()
        print(f"[Embedder] {len(new_files)} new files to process (skipping {len(files) - len(new_files)} processed)")
        
        if not new_files and file_provider is None:
            with self._lock:
                self._is_running = False
            self._update_state(state="completed", current_file="", processable_total=0, remaining_count=0)
            return False
        
        self._thread = threading.Thread(target=self._embedding_loop, args=(new_files,), daemon=True)
        self._thread.start()
        return True
    
    def stop(self) -> None:
        """임베딩 중지"""
        with self._lock:
            self._should_stop = True
        self._update_state(state="stopped")
        print("[Embedder] Stop requested")
    
    def is_running(self) -> bool:
        """실행 중인지 확인"""
        with self._lock:
            return self._is_running
    
    def get_status(self) -> dict:
        """현재 상태 반환"""
        with self._lock:
            extension_stats = {ext: dict(counter) for ext, counter in self._extension_stats.items()}
            return {
                "is_running": self._is_running,
                "processed_count": self._processed_count,
                "skip_count": self._skip_count,
                "error_count": self._error_count,
                "current_file": self._current_file,
                "last_error": self._last_error,
                "consecutive_load_failures": self._consecutive_load_failures,
                "backoff_until": self._backoff_until,
                "memory_wait_until": self._memory_wait_until,
                "memory_wait_attempts": self._memory_wait_attempts,
                "memory_wait_details": dict(self._memory_wait_details),
                "embedding_disabled_for_session": self._embedding_disabled_for_session,
                "last_vectorstore_error": self._last_vectorstore_error,
                "total_processed": len(self._processed_files),
                "extension_stats": extension_stats,
                "state": self._state,
                "source_total": self._source_total,
                "processable_total": self._processable_total,
                "current_index": self._current_index,
                "remaining_count": self._remaining_count,
                "last_status_updated_at": self._last_status_updated_at,
            }
    
    def process_single_file_synchronous(self, filepath: str) -> dict:
        """
        단일 파일 동기식 처리 (Active Learning / JIT Ingestion용)
        요청 받은 즉시 해당 파일을 임베딩하고 DB에 추가합니다.
        """
        with self._lock:
            print(f"[Embedder] JIT Processing Request: {filepath}")
            if self._embedding_disabled_for_session:
                self._set_last_failure("embedding_error", "Embedding disabled for this session after repeated VectorStore load failures")
                print(f"[Embedder] {self._last_error}")
                return self._jit_result(filepath, False)
            if not self._is_processable_file(filepath):
                self._set_last_failure("unsupported_extension", f"JIT file is not processable: {filepath}")
                print(f"[Embedder] {self._last_error}")
                return self._jit_result(filepath, False)
            
            # 모델/DB 로드 확인
            if not self._lazy_load_vectorstore():
                print("[Embedder] Failed to load vectorstore for JIT")
                self._last_error_category = self._last_error_category or "embedding_error"
                return self._jit_result(filepath, False, self._last_error_category or "embedding_error", self._last_error)
            
            # 이미 처리된 파일인지 확인
            if filepath in self._processed_files:
                # 파일이 있지만 DB에 없을 수도 있으므로 재처리 강제? 
                # 아니다, Active Learning은 'DB에 없는 것'을 대상으로 호출되므로 무조건 처리
                pass
                
            # 처리 수행
            success = self._process_file(filepath)
            
            if success:
                self._save_processed_file(filepath)
                print(f"[Embedder] JIT Processing Success: {filepath}")
                return self._jit_result(filepath, True, "ok", "")
            else:
                print(f"[Embedder] JIT Processing Failed: {filepath}")
                return self._jit_result(filepath, False)

    def get_processed_count(self) -> int:
        """총 처리된 파일 수"""
        return len(self._processed_files)


if __name__ == "__main__":
    print("Background Embedder 테스트")
    print("=" * 50)
    
    embedder = BackgroundEmbedder()
    status = embedder.get_status()
    print(f"총 처리된 파일: {status['total_processed']}")
