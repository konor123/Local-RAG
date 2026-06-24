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

# 환경 변수 설정 (저자원 모드)
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "2"  # 최소 스레드
os.environ["ANONYMIZED_TELEMETRY"] = "False"

PROCESSED_FILE = os.environ.get("PROCESSED_FILES_PATH", "./processed_files.txt")
VECTOR_STORE_PATH = "./chroma_db_ko"
LOG_FILE = "./embed_log.txt"

# 임베딩 가능한 확장자
EMBEDDABLE_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".pptx", ".ppt", ".txt", ".html", ".htm"
}

PRIORITY_EXTS = {
    ".xlsx": 0, ".xls": 0,
    ".docx": 1, ".doc": 1,
    ".hwp": 1, ".txt": 1,
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

# 파일 로거 설정
def _get_logger():
    logger = logging.getLogger("embedder")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    return logger

_log = _get_logger()


class BackgroundEmbedder:
    """저자원 백그라운드 임베딩 처리기"""
    
    def __init__(
        self,
        sleep_between_files: float = 5.0,  # 파일 간 대기 시간 (초)
        batch_size: int = 10,  # 배치 크기
        chunk_size: int = 500,  # 텍스트 청크 크기
        chunk_overlap: int = 50  # 청크 오버랩
    ):
        self.sleep_between_files = sleep_between_files
        self.batch_size = batch_size
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        self._model = None  # 지연 로딩
        self._vectorstore = None
        self._is_running = False
        self._should_stop = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # 상태 추적
        self._processed_count = 0
        self._skip_count = 0
        self._error_count = 0
        self._current_file = ""
        self._last_error = ""
        self._status_callback: Optional[Callable] = None
        self._extension_stats = defaultdict(Counter)
        
        # 처리된 파일 목록 로드
        self._processed_files: Set[str] = self._load_processed_files()

    def _record_status(self, filepath: str, status: str) -> None:
        ext = os.path.splitext(filepath)[1].lower() or "<no_ext>"
        with self._lock:
            self._extension_stats[ext][status] += 1
            self._extension_stats["__total__"][status] += 1
    
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
        
        try:
            from faiss_store import load_index, get_backend_name
            
            load_index()
            self._vectorstore = True  # 이름은 _vectorstore 유지 (호환)
            print(f"[Embedder] VectorStore index loaded successfully ({get_backend_name()})")
            return True
        except Exception as e:
            self._last_error = f"VectorStore load failed: {e}"
            print(f"[Embedder] {self._last_error}")
            return False
    
    def _process_file(self, filepath: str) -> bool:
        """단일 파일 처리"""
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        
        self._current_file = os.path.basename(filepath)
        
        # 확장자 필터
        ext = os.path.splitext(filepath)[1].lower()
        if _is_temporary_office_file(filepath):
            self._skip_count += 1
            self._record_status(filepath, "temporary_file")
            return False
        if ext not in EMBEDDABLE_EXTENSIONS:
            self._skip_count += 1
            self._record_status(filepath, "unsupported_extension")
            return False  # 지원하지 않는 확장자 → 무시 (로그 안 남김)
        
        # 파일 존재 확인
        if not os.path.exists(filepath):
            _log.debug(f"SKIP (not found): {filepath}")
            self._skip_count += 1
            self._record_status(filepath, "missing_file")
            return False
        
        try:
            # 파일 로드 (subprocess로 안전하게)
            import subprocess
            
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            
            # Windows에서 팝업 창 숨기기
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            
            result = subprocess.run(
                ["python", "worker_loader.py", filepath],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=120,
                env=env,
                startupinfo=startupinfo
            )
            
            if result.returncode != 0:
                _log.warning(f"FAIL (worker exit {result.returncode}): {self._current_file} | {result.stderr[:100]}")
                self._error_count += 1
                self._record_status(filepath, _classify_error_text(result.stderr))
                return False
            
            output = result.stdout.strip()
            if not output:
                _log.debug(f"SKIP (empty output): {self._current_file}")
                self._skip_count += 1
                self._record_status(filepath, "empty_output")
                return False
            if output == "ENCRYPTED_FILE":
                _log.debug(f"SKIP (encrypted): {self._current_file}")
                self._skip_count += 1
                self._record_status(filepath, "empty_or_encrypted")
                return False
            
            data = json.loads(output)
            if isinstance(data, dict) and data.get("__loader_error__"):
                category = data.get("category", "parse_error")
                if category in ("unsupported_extension", "empty_or_encrypted"):
                    self._skip_count += 1
                else:
                    self._error_count += 1
                self._record_status(filepath, category)
                _log.warning(f"FAIL ({category}): {self._current_file} | {data.get('detail', '')[:100]}")
                return False
            if not data:
                _log.debug(f"SKIP (no chunks): {self._current_file}")
                self._skip_count += 1
                self._record_status(filepath, "no_chunks")
                return False
            
            # VectorStore index 추가
            from faiss_store import add_documents, save_index
            
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
                self._record_status(filepath, "no_chunks")
                return False
            
            # 임베딩 생성 (모델 로딩)
            if not self._lazy_load_model():
                _log.error(f"FAIL (model not loaded): {self._current_file}")
                self._error_count += 1
                self._record_status(filepath, "embedding_error")
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
                self._processed_count += 1
                self._record_status(filepath, "ok")
                _log.info(f"OK ({len(final_docs)} chunks): {self._current_file}")
                return True
            else:
                _log.debug(f"SKIP (0 chunks after split): {self._current_file}")
                self._skip_count += 1
                self._record_status(filepath, "no_chunks")
                return False
            
        except subprocess.TimeoutExpired:
            self._last_error = f"Timeout: {self._current_file}"
            _log.warning(f"FAIL (timeout 120s): {self._current_file}")
            self._error_count += 1
            self._record_status(filepath, "timeout")
            return False
        except Exception as e:
            self._last_error = f"Error: {self._current_file} - {str(e)[:100]}"
            _log.error(f"FAIL (exception): {self._current_file} | {str(e)[:150]}")
            self._error_count += 1
            self._record_status(filepath, _classify_error_text(str(e)))
            return False
    
    def _embedding_loop(self, files: List[str]) -> None:
        """임베딩 메인 루프"""
        _log.info(f"=== Embedding loop START: {len(files)} files ===")
        print(f"[Embedder] Starting embedding loop for {len(files)} files")
        
        # 모델 및 벡터스토어 로드
        if not self._lazy_load_vectorstore():
            _log.error("VectorStore load FAILED — aborting loop")
            self._is_running = False
            return
        _log.info("VectorStore loaded OK")
        
        for i, filepath in enumerate(files):
            if self._should_stop:
                _log.info("Stop requested, exiting loop")
                break
            
            # 이미 처리된 파일 건너뛰기
            if filepath in self._processed_files:
                continue
            
            # 파일 처리
            success = self._process_file(filepath)
            
            if success:
                self._save_processed_file(filepath)
            
            # 콜백 호출
            if self._status_callback:
                self._status_callback(self._processed_count, len(files), self._current_file, success)
            
            # 진행 상황 로그 (100개마다)
            checked = i + 1
            if checked % 100 == 0:
                _log.info(f"Progress: checked={checked}/{len(files)} | ok={self._processed_count} | skip={self._skip_count} | err={self._error_count}")
            
            # 저자원 모드: 성공한 파일 후에만 대기 (스킵은 빠르게 넘김)
            if success and not self._should_stop:
                time.sleep(self.sleep_between_files)
        
        self._is_running = False
        self._current_file = ""
        _log.info(f"=== Embedding loop END: ok={self._processed_count} | skip={self._skip_count} | err={self._error_count} ===")
        print(f"[Embedder] Embedding loop finished. Processed: {self._processed_count}")
    
    def start(self, files: List[str], status_callback: Callable = None, limit: int = None) -> bool:
        """
        백그라운드 임베딩 시작
        
        Args:
            files: 처리할 파일 목록
            status_callback: 상태 콜백 (current, total, filename, success)
            limit: 테스트용 최대 처리 파일 수. None이면 제한 없음.
        
        Returns:
            시작 성공 여부
        """
        with self._lock:
            if self._is_running:
                return False
            
            self._is_running = True
            self._should_stop = False
            self._status_callback = status_callback
        
        # 새 파일만 필터링
        new_files = sorted([f for f in files if f not in self._processed_files], key=_priority)
        if limit:
            new_files = new_files[:limit]
        print(f"[Embedder] {len(new_files)} new files to process (skipping {len(files) - len(new_files)} processed)")
        
        if not new_files:
            self._is_running = False
            return False
        
        self._thread = threading.Thread(target=self._embedding_loop, args=(new_files,), daemon=True)
        self._thread.start()
        return True
    
    def stop(self) -> None:
        """임베딩 중지"""
        self._should_stop = True
        print("[Embedder] Stop requested")
    
    def is_running(self) -> bool:
        """실행 중인지 확인"""
        return self._is_running
    
    def get_status(self) -> dict:
        """현재 상태 반환"""
        extension_stats = {ext: dict(counter) for ext, counter in self._extension_stats.items()}
        return {
            "is_running": self._is_running,
            "processed_count": self._processed_count,
            "skip_count": self._skip_count,
            "error_count": self._error_count,
            "current_file": self._current_file,
            "last_error": self._last_error,
            "total_processed": len(self._processed_files),
            "extension_stats": extension_stats
        }
    
    def process_single_file_synchronous(self, filepath: str) -> bool:
        """
        단일 파일 동기식 처리 (Active Learning / JIT Ingestion용)
        요청 받은 즉시 해당 파일을 임베딩하고 DB에 추가합니다.
        """
        with self._lock:
            print(f"[Embedder] JIT Processing Request: {filepath}")
            
            # 모델/DB 로드 확인
            if not self._lazy_load_vectorstore():
                print("[Embedder] Failed to load vectorstore for JIT")
                return False
            
            # 이미 처리된 파일인지 확인
            if self._load_processed_files and filepath in self._processed_files:
                # 파일이 있지만 DB에 없을 수도 있으므로 재처리 강제? 
                # 아니다, Active Learning은 'DB에 없는 것'을 대상으로 호출되므로 무조건 처리
                pass
                
            # 처리 수행
            success = self._process_file(filepath)
            
            if success:
                self._save_processed_file(filepath)
                print(f"[Embedder] JIT Processing Success: {filepath}")
            else:
                print(f"[Embedder] JIT Processing Failed: {filepath}")
                
            return success

    def get_processed_count(self) -> int:
        """총 처리된 파일 수"""
        return len(self._processed_files)


if __name__ == "__main__":
    print("Background Embedder 테스트")
    print("=" * 50)
    
    embedder = BackgroundEmbedder()
    status = embedder.get_status()
    print(f"총 처리된 파일: {status['total_processed']}")
