# -*- coding: utf-8 -*-
"""
W Drive Cleanup Script
Removes all data related to W: drive from file cache, processed list, and Chroma DB.
"""
import os
import json
import shutil
from typing import List

FILE_LIST_CACHE = "./file_list_cache.json"
PROCESSED_FILES = "./processed_files.txt"
VECTOR_STORE_PATH = "./chroma_db_ko"

def clean_file_list_cache():
    """file_list_cache.json 정리"""
    print(f"Checking {FILE_LIST_CACHE}...")
    if not os.path.exists(FILE_LIST_CACHE):
        print("  File not found.")
        return

    try:
        with open(FILE_LIST_CACHE, "r", encoding="utf-8") as f:
            files = json.load(f)
        
        original_count = len(files)
        # W:/ 또는 W:\로 시작하지 않는 파일만 유지
        new_files = [
            f for f in files 
            if not (f.upper().startswith("W:/") or f.upper().startswith("W:\\"))
        ]
        
        removed_count = original_count - len(new_files)
        
        if removed_count > 0:
            with open(FILE_LIST_CACHE, "w", encoding="utf-8") as f:
                json.dump(new_files, f, ensure_ascii=False, indent=2)
            print(f"  Removed {removed_count} files from cache.")
        else:
            print("  No W: drive files found in cache.")
            
    except Exception as e:
        print(f"  Error cleaning cache: {e}")

def clean_processed_files():
    """processed_files.txt 정리"""
    print(f"Checking {PROCESSED_FILES}...")
    if not os.path.exists(PROCESSED_FILES):
        print("  File not found.")
        return

    try:
        with open(PROCESSED_FILES, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        original_count = len(lines)
        new_lines = [
            line for line in lines 
            if not (line.strip().upper().startswith("W:/") or line.strip().upper().startswith("W:\\"))
        ]
        
        removed_count = original_count - len(new_lines)
        
        if removed_count > 0:
            with open(PROCESSED_FILES, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            print(f"  Removed {removed_count} entries from processed list.")
        else:
            print("  No W: drive entries found in processed list.")
            
    except Exception as e:
        print(f"  Error cleaning processed list: {e}")

def clean_chroma_db():
    """Chroma DB 정리"""
    print(f"Checking Chroma DB at {VECTOR_STORE_PATH}...")
    if not os.path.exists(VECTOR_STORE_PATH):
        print("  Vector store directory not found.")
        return

    try:
        import chromadb
        from chromadb.config import Settings
        
        chroma_settings = Settings(anonymized_telemetry=False)
        client = chromadb.PersistentClient(path=VECTOR_STORE_PATH, settings=chroma_settings)
        
        collection = client.get_collection("langchain")
        
        # 모든 데이터의 메타데이터 조회
        # get() 메서드로 모든 ID와 메타데이터 가져오기
        result = collection.get()
        ids = result['ids']
        metadatas = result['metadatas']
        
        if not ids:
            print("  No embeddings found in DB.")
            return
            
        ids_to_delete = []
        for i, meta in enumerate(metadatas):
            if meta and 'source' in meta:
                source = meta['source'].upper()
                if source.startswith("W:/") or source.startswith("W:\\"):
                    ids_to_delete.append(ids[i])
        
        if ids_to_delete:
            print(f"  Found {len(ids_to_delete)} embeddings from W: drive.")
            print("  Deleting..." )
            # 배치로 삭제 (한 번에 너무 많으면 문제될 수 있으므로)
            batch_size = 5000
            for i in range(0, len(ids_to_delete), batch_size):
                batch = ids_to_delete[i:i+batch_size]
                collection.delete(ids=batch)
                print(f"    Deleted batch {i // batch_size + 1}")
            print("  Deletion complete.")
        else:
            print("  No W: drive embeddings found.")
            
    except ImportError:
        print("  Error: chromadb module not found. Run 'pip install chromadb'")
    except Exception as e:
        print(f"  Error cleaning Chroma DB: {e}")

if __name__ == "__main__":
    print("Starting W Drive Cleanup...")
    print("=" * 50)
    
    clean_file_list_cache()
    print("-" * 30)
    
    clean_processed_files()
    print("-" * 30)
    
    clean_chroma_db()
    print("=" * 50)
    print("Cleanup Finished.")
