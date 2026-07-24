"""
File System Tools for Agentic Chatbot
Provides tools for searching, reading, and exploring network drives.
"""
import os
import glob
import subprocess
from typing import List, Dict, Optional
from pathlib import Path
from drive_manager import filter_walk_dirs, get_exclude_dir_names, get_search_roots
from runtime_paths import runtime_path

# Search roots are resolved at call time so late-mounted network drives are not
# missed. Keep this only for backward compatibility with external imports.
DRIVES = get_search_roots()
MAX_RESULTS = 100
MAX_FILE_SIZE = 100 * 1024  # 100KB max for file reading

# Global cache for file paths
_FILE_CACHE = None
_FILE_CACHE_INDEX = None  # Pre-processed index: [(filename, filepath), ...]
_BIGRAM_INDEX = None      # Inverted index: {bigram: set(indices)}
_EXT_INDEX = None         # Extension index: {".xlsx": set(indices)}
_DRIVE_INDEX = None       # Drive index: {"X": set(indices)}
_CACHE_FILE = runtime_path("file_list_cache.json")
_CACHE_MTIME = None


def _build_inverted_index():
    """캐시 로딩 후 역인덱스 구축 (바이그램 + 확장자 + 드라이브)"""
    global _BIGRAM_INDEX, _EXT_INDEX, _DRIVE_INDEX
    import time
    
    start = time.time()
    _BIGRAM_INDEX = {}
    _EXT_INDEX = {}
    _DRIVE_INDEX = {}
    
    for idx, (filename, filepath) in enumerate(_FILE_CACHE_INDEX):
        fname_lower = filename.lower()
        
        # 1. 바이그램 인덱스 (2글자 부분 문자열)
        for i in range(len(fname_lower) - 1):
            bigram = fname_lower[i:i+2]
            if bigram not in _BIGRAM_INDEX:
                _BIGRAM_INDEX[bigram] = set()
            _BIGRAM_INDEX[bigram].add(idx)
        
        # 2. 확장자 인덱스
        ext = os.path.splitext(fname_lower)[1]
        if ext:
            if ext not in _EXT_INDEX:
                _EXT_INDEX[ext] = set()
            _EXT_INDEX[ext].add(idx)
        
        # 3. 드라이브 인덱스
        drive_letter = filepath[0].upper() if filepath else ''
        if drive_letter:
            if drive_letter not in _DRIVE_INDEX:
                _DRIVE_INDEX[drive_letter] = set()
            _DRIVE_INDEX[drive_letter].add(idx)
    
    elapsed = time.time() - start
    print(f"[Cache] Inverted index built: {len(_BIGRAM_INDEX):,} bigrams, "
          f"{len(_EXT_INDEX)} extensions, {len(_DRIVE_INDEX)} drives ({elapsed:.2f}s)")


def _extract_keywords_from_pattern(pattern: str) -> tuple:
    """
    Glob 패턴에서 키워드와 확장자를 추출합니다.
    예: '*견적*삼성*.xlsx' → (['견적', '삼성'], '.xlsx')
    """
    import re
    
    # 확장자 추출
    ext = None
    ext_match = re.search(r'\.(\w+)$', pattern)
    if ext_match:
        ext = '.' + ext_match.group(1).lower()
    
    # * 와 ? 기준으로 분할하여 키워드 추출 (2글자 이상만)
    parts = re.split(r'[*?]+', pattern)
    keywords = [p.lower() for p in parts if len(p) >= 2 and not p.startswith('.')]
    
    return keywords, ext


def _search_inverted(keywords: list, ext: str = None, drive_letters: set = None) -> set:
    """
    역인덱스로 후보 집합을 빠르게 구합니다.
    
    Returns:
        set of indices into _FILE_CACHE_INDEX
    """
    if _BIGRAM_INDEX is None:
        return None  # 역인덱스 미구축
    
    candidate_sets = []
    
    # 1. 키워드별 바이그램 교집합
    for kw in keywords:
        if len(kw) < 2:
            continue
        # 키워드의 모든 바이그램이 포함된 파일만 후보
        bigram_sets = []
        for i in range(len(kw) - 1):
            bigram = kw[i:i+2]
            if bigram in _BIGRAM_INDEX:
                bigram_sets.append(_BIGRAM_INDEX[bigram])
            else:
                bigram_sets.append(set())  # 이 바이그램이 없으면 결과 0
                break
        
        if bigram_sets:
            kw_candidates = bigram_sets[0]
            for bs in bigram_sets[1:]:
                kw_candidates = kw_candidates & bs  # 교집합
            candidate_sets.append(kw_candidates)
    
    # 2. 확장자 필터
    if ext and ext in _EXT_INDEX:
        candidate_sets.append(_EXT_INDEX[ext])
    
    # 3. 드라이브 필터
    if drive_letters:
        drive_candidates = set()
        for dl in drive_letters:
            if dl in _DRIVE_INDEX:
                drive_candidates |= _DRIVE_INDEX[dl]
        if drive_candidates:
            candidate_sets.append(drive_candidates)
    
    if not candidate_sets:
        return None  # 필터 조건 없음 → 전체 스캔
    
    # 모든 조건의 교집합
    result = candidate_sets[0]
    for cs in candidate_sets[1:]:
        result = result & cs
    
    return result


def invalidate_file_cache() -> None:
    """Force the next file search to reload file_list_cache.json."""
    global _FILE_CACHE, _FILE_CACHE_INDEX, _BIGRAM_INDEX, _EXT_INDEX, _DRIVE_INDEX, _CACHE_MTIME
    _FILE_CACHE = None
    _FILE_CACHE_INDEX = None
    _BIGRAM_INDEX = None
    _EXT_INDEX = None
    _DRIVE_INDEX = None
    _CACHE_MTIME = None


def _load_file_cache():
    """Load file list cache into memory for fast searching."""
    global _FILE_CACHE, _FILE_CACHE_INDEX, _CACHE_MTIME
    current_mtime = os.path.getmtime(_CACHE_FILE) if os.path.exists(_CACHE_FILE) else None
    if _FILE_CACHE is not None and _CACHE_MTIME == current_mtime:
        return _FILE_CACHE
    
    import json
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                _FILE_CACHE = json.load(f)
            _CACHE_MTIME = current_mtime
            
            # Pre-process: create index with (filename, filepath) tuples
            _FILE_CACHE_INDEX = []
            for filepath in _FILE_CACHE:
                filename = os.path.basename(filepath)
                _FILE_CACHE_INDEX.append((filename, filepath))
            
            # 역인덱스 구축
            _build_inverted_index()
            
            print(f"[Cache] Loaded {len(_FILE_CACHE):,} files from cache (indexed)")
        else:
            _FILE_CACHE = []
            _FILE_CACHE_INDEX = []
            _CACHE_MTIME = current_mtime
            print("[Cache] No cache file found, using empty cache")
    except Exception as e:
        _FILE_CACHE = []
        _FILE_CACHE_INDEX = []
        print(f"[Cache] Error loading cache: {e}")
    
    return _FILE_CACHE


def find_similar_files(keywords: List[str], max_results: int = 20) -> Dict:
    """
    파일 캐시에서 키워드가 부분적으로 일치하는 파일들을 찾습니다.
    스마트 쿼리 재작성에 사용됩니다.
    
    Args:
        keywords: 검색할 키워드 리스트
        max_results: 반환할 최대 결과 수
    
    Returns:
        Dict with similar files and extracted patterns
    """
    import re
    from collections import Counter
    
    _load_file_cache()
    
    if not _FILE_CACHE_INDEX or not keywords:
        return {"files": [], "suggested_keywords": [], "count": 0}
    
    # 키워드 정제 (2글자 이상만)
    keywords = [k.lower() for k in keywords if len(k) >= 2]
    if not keywords:
        return {"files": [], "suggested_keywords": [], "count": 0}
    
    matching_files = []
    
    # 캐시에서 부분 매칭 검색
    for filename, filepath in _FILE_CACHE_INDEX:
        filename_lower = filename.lower()
        
        # 키워드 중 하나라도 포함되면 매칭
        matched_keywords = [kw for kw in keywords if kw in filename_lower]
        if matched_keywords:
            matching_files.append({
                "filename": filename,
                "path": filepath,
                "matched": matched_keywords,
                "score": len(matched_keywords)
            })
        
        if len(matching_files) >= max_results * 5:  # 후처리를 위해 더 많이 수집
            break
    
    # 매칭 점수순 정렬
    matching_files.sort(key=lambda x: x["score"], reverse=True)
    matching_files = matching_files[:max_results]
    
    # 파일명에서 유용한 키워드 추출
    word_freq = Counter()
    for f in matching_files:
        # 파일명을 단어로 분리 (특수문자 기준)
        words = re.split(r'[\s_\-\.\(\)\[\]]+', f["filename"])
        for word in words:
            # 의미있는 단어만 (2글자 이상, 숫자/확장자 제외)
            if (len(word) >= 2 
                and not word.isdigit() 
                and word.lower() not in ['pdf', 'xlsx', 'docx', 'hwp', 'pptx', 'jpg', 'png']
                and word.lower() not in keywords):  # 원래 키워드 제외
                word_freq[word] += 1
    
    # 가장 많이 등장하는 단어들을 제안 키워드로
    suggested_keywords = [w for w, count in word_freq.most_common(5) if count >= 2]
    
    return {
        "files": matching_files,
        "suggested_keywords": suggested_keywords,
        "count": len(matching_files)
    }



def search_files(pattern: str, drives: Optional[List[str]] = None, use_cache: bool = True, sort_by: str = "date_newest", include_keywords: List[str] = None) -> Dict:
    """
    Search for files by name pattern.
    Uses cached file list for instant search (< 1 second).
    
    Args:
        pattern: Search pattern (e.g., "*삼성*견적*", "*.xlsx")
        drives: List of drives to filter (default: all drives)
        use_cache: Use cached file list for fast search (default: True)
        sort_by: Sort results by "name", "date_newest", or "date_oldest" (default: date_newest)
        include_keywords: List of keywords that MUST be present in the filename (AND condition, case-insensitive)
    
    Returns:
        Dict with results and metadata
    """
    import fnmatch
    import time
    import re
    
    start_time = time.time()
    
    # Pre-compile pattern to regex for faster matching
    regex_pattern = fnmatch.translate(pattern)
    regex = re.compile(regex_pattern, re.IGNORECASE)
    
    if drives is None:
        drives = get_search_roots()
    drives = drives or get_search_roots()
    
    results = []
    
    if use_cache:
        # Fast cache-based search using pre-processed index
        _load_file_cache()  # Ensure cache is loaded
        
        if _FILE_CACHE_INDEX:
            # Pre-compute drive prefixes for faster matching
            drive_letters = set()
            if drives:
                for d in drives:
                    drive_letters.add(d[0].upper())
            
            # === 역인덱스 기반 후보 축소 ===
            keywords, ext = _extract_keywords_from_pattern(pattern)
            candidates = _search_inverted(keywords, ext, drive_letters)
            
            if candidates is not None:
                # 역인덱스 히트: 후보만 정밀 검사
                scan_count = len(candidates)
                for idx in candidates:
                    filename, filepath = _FILE_CACHE_INDEX[idx]
                    
                    if regex.match(filename):
                        if include_keywords:
                            try:
                                filename_lower = filename.lower()
                                if not all(k.lower() in filename_lower for k in include_keywords):
                                    continue
                            except:
                                pass
                        
                        results.append({"path": filepath, "name": filename})
                        
                        if sort_by == "name" and len(results) >= MAX_RESULTS:
                            break
                        elif len(results) >= MAX_RESULTS * 50:
                            break
            else:
                # 역인덱스 미스: 기존 방식 폴백 (전체 순회)
                scan_count = len(_FILE_CACHE_INDEX)
                for filename, filepath in _FILE_CACHE_INDEX:
                    if drive_letters:
                        first_char = filepath[0].upper() if filepath else ''
                        if first_char not in drive_letters:
                            continue
                    
                    if regex.match(filename):
                        if include_keywords:
                            try:
                                filename_lower = filename.lower()
                                if not all(k.lower() in filename_lower for k in include_keywords):
                                    continue
                            except:
                                pass
                        
                        results.append({"path": filepath, "name": filename})
                        
                        if sort_by == "name" and len(results) >= MAX_RESULTS:
                            break
                        elif len(results) >= MAX_RESULTS * 50:
                            break
            
            # Sort results if requested
            if sort_by in ["date_newest", "date_oldest"] and results:
                import datetime
                
                def get_sort_key(item):
                    path = item["path"]
                    name = item["name"]
                    score = 0
                    
                    # 심플 정렬: 오직 파일 수정 시간만 사용
                    try:
                        score = os.path.getmtime(path)
                    except:
                        score = 0
                    
                    return score

                reverse = (sort_by == "date_newest")
                results.sort(key=get_sort_key, reverse=reverse)
            
            # Limit results
            results = results[:MAX_RESULTS]
            
            # Add formatted date string for LLM
            import datetime
            for r in results:
                try:
                    mtime = os.path.getmtime(r["path"])
                    dt = datetime.datetime.fromtimestamp(mtime)
                    r["last_modified"] = dt.strftime("%Y-%m-%d")
                except:
                    r["last_modified"] = "Unknown"
            
            elapsed = time.time() - start_time
            total_files = len(_FILE_CACHE_INDEX)
            return {
                "count": len(results),
                "results": results,
                "pattern": pattern,
                "sort_by": sort_by,
                "search_time": f"{elapsed:.3f}s",
                "source": "cache",
                "index_mode": "inverted" if candidates is not None else "full_scan",
                "scanned": f"{scan_count:,} / {total_files:,}",
                "truncated": len(results) == MAX_RESULTS
            }
    
    # Fallback: Live search (slow, only if cache not available)
    timeout = 30
    exclude_names = get_exclude_dir_names()
    
    for drive in drives:
        if not os.path.exists(drive):
            continue
        if time.time() - start_time > timeout:
            break
        try:
            for root, dirs, files in os.walk(drive):
                filter_walk_dirs(dirs, root, exclude_names)
                if time.time() - start_time > timeout:
                    break
                for filename in files:
                    if fnmatch.fnmatch(filename, pattern):
                        if include_keywords:
                            try:
                                filename_lower = filename.lower()
                                if not all(k.lower() in filename_lower for k in include_keywords):
                                    continue
                            except Exception:
                                pass
                        filepath = os.path.join(root, filename)
                        results.append({"path": filepath, "name": filename})
                        if len(results) >= MAX_RESULTS * 50:
                            break
                if len(results) >= MAX_RESULTS * 50:
                    break
        except Exception:
            continue
    
    elapsed = time.time() - start_time
    return {
        "count": len(results),
        "results": results[:MAX_RESULTS],
        "pattern": pattern,
        "search_time": f"{elapsed:.2f}s",
        "source": "live",
        "truncated": len(results) > MAX_RESULTS
    }


def read_file(path: str, start_line: int = 1, max_lines: int = 100) -> Dict:
    """
    Read content from a file.
    
    Args:
        path: Absolute file path
        start_line: Starting line number (1-indexed)
        max_lines: Maximum lines to read
    
    Returns:
        Dict with file content and metadata
    """
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    
    file_size = os.path.getsize(path)
    if file_size > MAX_FILE_SIZE:
        return {"error": f"File too large ({file_size} bytes). Max: {MAX_FILE_SIZE} bytes"}
    
    ext = os.path.splitext(path)[1].lower()
    
    # Text files
    if ext in [".txt", ".md", ".py", ".json", ".csv", ".log"]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            selected = lines[start_line-1:start_line-1+max_lines]
            content = "".join(selected)
            
            return {
                "path": path,
                "content": content,
                "start_line": start_line,
                "end_line": start_line + len(selected) - 1,
                "total_lines": total_lines
            }
        except Exception as e:
            return {"error": str(e)}
    
    # Excel/Word/PDF - just return metadata
    elif ext in [".xlsx", ".xls", ".docx", ".doc", ".pdf"]:
        return {
            "path": path,
            "type": ext,
            "size": file_size,
            "note": "Office/PDF files require specialized parsing. Use grep_content for keyword search."
        }
    
    else:
        return {"error": f"Unsupported file type: {ext}"}


def grep_content(keyword: str, path: str = None, drives: Optional[List[str]] = None) -> Dict:
    """
    Search for keyword inside files using grep/findstr.
    
    Args:
        keyword: Keyword to search for
        path: Specific path/folder to search (optional)
        drives: Drives to search if no path specified
    
    Returns:
        Dict with matching files and snippets
    """
    if drives is None:
        drives = get_search_roots()
    drives = drives or get_search_roots()
    
    results = []
    
    search_paths = [path] if path else drives
    
    for search_path in search_paths:
        if not os.path.exists(search_path):
            continue
        
        try:
            # Use findstr on Windows for text files
            cmd = [
                "findstr", "/S", "/I", "/M",
                keyword,
                os.path.join(search_path, "*.txt"),
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="cp949",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            
            if result.stdout:
                files = result.stdout.strip().split("\n")
                for f in files[:MAX_RESULTS]:
                    if f.strip():
                        results.append({
                            "path": f.strip(),
                            "name": os.path.basename(f.strip())
                        })
        except:
            continue
    
    return {
        "keyword": keyword,
        "count": len(results),
        "results": results[:MAX_RESULTS]
    }


def list_directory(path: str, max_items: int = 100) -> Dict:
    """
    List contents of a directory.
    
    Args:
        path: Directory path
        max_items: Maximum items to return
    
    Returns:
        Dict with directory contents
    """
    if not os.path.exists(path):
        return {"error": f"Path not found: {path}"}
    
    if not os.path.isdir(path):
        return {"error": f"Not a directory: {path}"}
    
    try:
        items = []
        for item in os.listdir(path)[:max_items]:
            item_path = os.path.join(path, item)
            try:
                is_dir = os.path.isdir(item_path)
                size = os.path.getsize(item_path) if not is_dir else None
                items.append({
                    "name": item,
                    "type": "directory" if is_dir else "file",
                    "size": size
                })
            except:
                continue
        
        return {
            "path": path,
            "count": len(items),
            "items": items,
            "truncated": len(os.listdir(path)) > max_items
        }
    except Exception as e:
        return {"error": str(e)}


def save_memory(key: str, value: str, memory_file: str = None) -> Dict:
    """
    Save a piece of learned information to persistent memory.
    
    Args:
        key: Topic/keyword for the memory
        value: Information to remember
        memory_file: Path to memory file
    
    Returns:
        Confirmation dict
    """
    import json
    from datetime import datetime
    memory_file = memory_file or runtime_path("chat_memory.json")
    
    try:
        # Load existing memory
        if os.path.exists(memory_file):
            with open(memory_file, "r", encoding="utf-8") as f:
                memory = json.load(f)
        else:
            memory = {}
        
        # Add new memory
        memory[key] = {
            "value": value,
            "timestamp": datetime.now().isoformat()
        }
        
        # Save
        with open(memory_file, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
        
        return {"status": "saved", "key": key}
    
    except Exception as e:
        return {"error": str(e)}


def recall_memory(keyword: str = None, memory_file: str = None) -> Dict:
    """
    Recall learned information from memory.
    
    Args:
        keyword: Optional keyword to filter memories
        memory_file: Path to memory file
    
    Returns:
        Dict with matching memories
    """
    import json
    memory_file = memory_file or runtime_path("chat_memory.json")
    
    try:
        if not os.path.exists(memory_file):
            return {"memories": [], "count": 0}
        
        with open(memory_file, "r", encoding="utf-8") as f:
            memory = json.load(f)
        
        if keyword:
            # Filter by keyword
            results = {k: v for k, v in memory.items() 
                      if keyword.lower() in k.lower() or keyword.lower() in v.get("value", "").lower()}
        else:
            results = memory
        
        return {
            "memories": results,
            "count": len(results)
        }
    
    except Exception as e:
        return {"error": str(e)}


def search_content(query: str, k: int = 4) -> Dict:
    """
    RAG 벡터 검색으로 파일 내용을 검색합니다.
    의미 기반 검색으로 관련 문서 청크를 찾습니다.
    
    Args:
        query: 검색 쿼리 (자연어)
        k: 반환할 문서 수 (기본값: 4)
    
    Returns:
        Dict with matching document chunks and sources
    """
    try:
        from rag_engine import get_embeddings
        from faiss_store import search_similar
        
        # 1. 임베딩 생성
        embeddings = get_embeddings()
        query_vector = embeddings.embed_query(query)
        
        # 2. VectorStore 벡터 유사도 검색 (+ 키워드 부스트)
        faiss_results = search_similar(query_vector, query, k=k)
        
        results = []
        sources = set()
        
        for doc in faiss_results:
            source = doc.get("source", "Unknown")
            sources.add(source)
            results.append({
                "content": doc.get("content", "")[:500],
                "source": source,
                "metadata": doc.get("metadata", {}),
                "score": doc.get("score", 0)
            })

        # Atom hits are candidates only: atom_index promotes them to active
        # parent chunks before they can enter this public result contract.
        try:
            from atom_index import search_parent_chunks

            atom_parents = search_parent_chunks(query_vector, query, k=k) if len(results) < k else []
            seen_parent_ids = {item.get("metadata", {}).get("parent_chunk_id") for item in results}
            for parent in atom_parents:
                parent_id = parent.get("metadata", {}).get("parent_chunk_id")
                if parent_id and parent_id in seen_parent_ids:
                    continue
                sources.add(parent.get("source", "Unknown"))
                results.append({
                    "content": parent.get("content", "")[:500],
                    "source": parent.get("source", "Unknown"),
                    "metadata": parent.get("metadata", {}),
                    "score": parent.get("score", 0),
                    "source_engine": parent.get("source_engine", "atom_parent_vector"),
                })
                if parent_id:
                    seen_parent_ids.add(parent_id)
                if len(results) >= k:
                    break
        except Exception:
            # Atom indexing is an optional candidate source; retain the
            # established main-vector result contract on any atom failure.
            pass

        return {
            "query": query,
            "count": len(results),
            "results": results,
            "sources": list(sources)
        }
    
    except Exception as e:
        return {"error": f"RAG 검색 오류: {str(e)}"}


def search_metadata_content(query: str, k: int = 5) -> Dict:
    """Search the optional SQLite/FTS5 sidecar without replacing vector search.

    This is an architecture Phase 4 validation path. It is disabled by default
    and should be used side-by-side with vector search until quality is proven.
    """
    try:
        from config_manager import load_config

        cfg = load_config().get("metadata_index", {})
        if not cfg.get("enabled", False) or not cfg.get("fts_search_enabled", False):
            return {"query": query, "count": 0, "results": [], "sources": [], "disabled": True}

        from sqlite_index import search_fts

        rows = search_fts(query, k=k, db_path=cfg.get("path"))
        results = []
        sources = []
        seen_sources = set()
        for row in rows:
            source = row.get("source", "")
            if source and source not in seen_sources:
                sources.append(source)
                seen_sources.add(source)
            results.append({
                "content": row.get("content", "")[:500],
                "source": source,
                "metadata": row.get("metadata", {}),
                "score": row.get("score", 0),
                "source_engine": "sqlite_fts5",
            })
        return {
            "query": query,
            "count": len(results),
            "results": results,
            "sources": sources,
            "source": "sqlite_fts5",
        }
    except Exception as e:
        return {"error": f"SQLite FTS 검색 오류: {str(e)}", "query": query, "count": 0, "results": [], "sources": []}


def search_hybrid(query: str, k: int = 5) -> Dict:
    """Filename/path search and vector content search fused with RRF."""
    try:
        from hybrid_search import hybrid_search

        return hybrid_search(query, k=k)
    except Exception as e:
        return {"error": f"하이브리드 검색 오류: {str(e)}", "query": query, "count": 0, "results": [], "sources": []}

# Tool definitions for LLM Function Calling
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "네트워크 드라이브에서 파일명으로 파일을 검색합니다. 최신 파일을 찾을 때는 sort_by='date_newest'를 사용하세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "검색할 파일명 패턴 (glob 형식). 예: *삼성*견적*.xlsx"
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["name", "date_newest", "date_oldest"],
                        "description": "정렬 기준. name=이름순, date_newest=최신순, date_oldest=오래된순. 기본값: name"
                    },
                    "include_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "파일명에 반드시 포함되어야 하는 키워드 목록 (AND 조건)"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "특정 파일의 내용을 읽습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "읽을 파일의 전체 경로"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep_content",
            "description": "파일 내용에서 키워드를 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색할 키워드"
                    },
                    "path": {
                        "type": "string",
                        "description": "검색할 폴더 경로 (선택사항)"
                    }
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "폴더의 내용을 나열합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "탐색할 폴더 경로"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "중요한 정보를 기억합니다. 사용자가 알려주는 정보를 저장할 때 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "기억할 정보의 주제/키워드"
                    },
                    "value": {
                        "type": "string",
                        "description": "기억할 내용"
                    }
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "이전에 저장한 정보를 떠올립니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색할 키워드 (선택사항)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "RAG 벡터 검색으로 파일 내용을 검색합니다. 의미 기반 검색으로 관련 문서를 찾습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 쿼리 (자연어, 예: 불꽃감지기 설치 방법)"
                    },
                    "k": {
                        "type": "integer",
                        "description": "반환할 문서 수 (기본값: 4)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_hybrid",
            "description": "파일명 검색과 RAG 내용 검색을 RRF로 병합해 관련 문서를 찾습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 자연어 쿼리"
                    },
                    "k": {
                        "type": "integer",
                        "description": "반환할 문서 수 (기본값: 5)"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


def execute_tool(tool_name: str, arguments: dict) -> Dict:
    """Execute a tool by name with given arguments."""
    tools = {
        "search_files": search_files,
        "read_file": read_file,
        "grep_content": grep_content,
        "list_directory": list_directory,
        "save_memory": save_memory,
        "recall_memory": recall_memory,
        "search_content": search_content,
        "search_hybrid": search_hybrid
    }
    
    if tool_name not in tools:
        return {"error": f"Unknown tool: {tool_name}"}
    
    try:
        return tools[tool_name](**arguments)
    except Exception as e:
        return {"error": str(e)}
