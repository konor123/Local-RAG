# -*- coding: utf-8 -*-
"""
Cache Manager - 파일 목록 캐시 관리
네트워크 드라이브를 스캔하고 파일 목록 캐시를 갱신합니다.
"""
import os
import json
import time
import threading
from typing import List, Set, Optional
from datetime import datetime
from drive_manager import filter_walk_dirs, get_exclude_dir_names, get_search_roots
from runtime_paths import runtime_path

# 검색 대상 드라이브: 기본은 현재 연결된 전체 드라이브
DRIVES = get_search_roots()
CACHE_FILE = runtime_path("file_list_cache.json")

# 지원하는 파일 확장자
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".pptx", ".ppt", ".dwg", ".txt", ".html", ".htm"
}


class CacheManager:
    """파일 목록 캐시 관리자"""
    
    def __init__(self, cache_file: str = CACHE_FILE, drives: List[str] = None):
        self.cache_file = cache_file
        self.drives = drives or get_search_roots()
        self._cache: List[str] = []
        self._last_refresh: Optional[datetime] = None
        self._lock = threading.Lock()
        self._is_refreshing = False
        
        # 시작 시 기존 캐시 로드
        self._load_cache()
    
    def _load_cache(self) -> None:
        """기존 캐시 파일 로드"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                print(f"[Cache] Loaded {len(self._cache)} files from cache")
            except Exception as e:
                print(f"[Cache] Error loading cache: {e}")
                self._cache = []
    
    def _save_cache(self) -> None:
        """캐시를 파일로 저장"""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            print(f"[Cache] Saved {len(self._cache)} files to cache")
            try:
                from tools import invalidate_file_cache
                invalidate_file_cache()
            except Exception:
                pass
        except Exception as e:
            print(f"[Cache] Error saving cache: {e}")
    
    def refresh_cache(self, callback=None) -> int:
        """
        파일 목록 스캔 및 캐시 갱신
        
        Args:
            callback: 진행 상황 콜백 (drive, count)
        
        Returns:
            새로 발견된 파일 수
        """
        if self._is_refreshing:
            return 0
        
        self._is_refreshing = True
        old_count = len(self._cache)
        
        try:
            all_files = []
            
            self.drives = get_search_roots()
            exclude_names = get_exclude_dir_names()
            for drive in self.drives:
                if not os.path.exists(drive):
                    print(f"[Cache] Drive {drive} not available, skipping")
                    continue
                
                print(f"[Cache] Scanning {drive}...")
                drive_count = 0
                
                try:
                    for root, dirs, files in os.walk(drive):
                        filter_walk_dirs(dirs, root, exclude_names)
                        for file in files:
                            ext = os.path.splitext(file)[1].lower()
                            if ext in SUPPORTED_EXTENSIONS:
                                filepath = os.path.join(root, file)
                                all_files.append(filepath)
                                drive_count += 1
                except Exception as e:
                    print(f"[Cache] Error scanning {drive}: {e}")
                
                print(f"[Cache] Found {drive_count} files in {drive}")
                
                if callback:
                    callback(drive, drive_count)
            
            with self._lock:
                self._cache = all_files
                self._last_refresh = datetime.now()
            
            self._save_cache()
            
            new_count = len(self._cache) - old_count
            print(f"[Cache] Refresh complete. Total: {len(self._cache)}, New: {new_count}")
            return new_count
            
        finally:
            self._is_refreshing = False
    
    def get_all_files(self) -> List[str]:
        """캐시된 모든 파일 목록 반환"""
        with self._lock:
            return self._cache.copy()
    
    def get_embeddable_files(self) -> List[str]:
        """임베딩 가능한 파일만 반환 (supported extensions only)"""
        with self._lock:
            return [f for f in self._cache 
                    if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS]
    
    def get_file_count(self) -> int:
        """캐시된 파일 수 반환"""
        return len(self._cache)
    
    def get_last_refresh(self) -> Optional[datetime]:
        """마지막 갱신 시간 반환"""
        return self._last_refresh
    
    def get_new_files(self, processed_files: Set[str]) -> List[str]:
        """
        처리되지 않은 새 파일 목록 반환
        
        Args:
            processed_files: 이미 처리된 파일 경로 집합
        
        Returns:
            새 파일 경로 목록
        """
        with self._lock:
            return [f for f in self._cache if f not in processed_files]
    
    def is_refreshing(self) -> bool:
        """현재 갱신 중인지 확인"""
        return self._is_refreshing


if __name__ == "__main__":
    # 테스트
    print("Cache Manager 테스트")
    print("=" * 50)
    
    manager = CacheManager()
    print(f"현재 캐시: {manager.get_file_count()} 파일")
    
    # 갱신 테스트 (시간이 오래 걸릴 수 있음)
    # count = manager.refresh_cache()
    # print(f"새 파일: {count}")
