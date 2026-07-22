# v1.5.2

## Fixes

- Fixed FAISS vector-store persistence to write `index.faiss` atomically via a temporary file and replace operation.
- Added selective corruption recovery for unreadable persisted vector indexes while preserving memory/size guard failures.
- Prevented missing FAISS dependency errors from moving persisted index files into corruption backups.
- Reduced repeated JIT learning failures by stopping remaining JIT files after a VectorStore embedding error.

## Recovery

- Added `rebuild_faiss_index.py` to rebuild a FAISS index from an intact `metadata.jsonl` sidecar with resumable checkpoints.
