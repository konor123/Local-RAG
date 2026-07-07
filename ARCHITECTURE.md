# OSL AI Assistant Architecture

## Purpose

OSL AI Assistant is a Windows desktop assistant for internal document search and RAG-based Q&A. The current architecture is Python/PySide6 with local Ollama, cached filename search, background embedding, and FAISS/TurboVec vector storage.

This document records the current system and the Docufinder-inspired roadmap adopted for OSL. Search operators such as `ext:` or `path:` are intentionally out of scope; the goal is to improve retrieval quality without asking users to learn query syntax.

## Current Data Flow

```text
Network/local drives
  -> drive_manager.py discovers searchable roots
  -> cache_manager.py scans supported files into file_list_cache.json
  -> tools.py builds an in-memory filename inverted index
  -> background_embedder.py loads files gradually
  -> worker_loader.py / ingest.py parse documents
  -> faiss_store.py stores vectors plus JSONL metadata
  -> tools.py exposes search_files and search_content
  -> unified_engine.py plans/searches/synthesizes answers
  -> native_ui.py renders chat, sources, and file-open links
```

## Current Search Paths

### Filename/path search

- Entry point: `tools.search_files()`
- Data source: `%LOCALAPPDATA%/OSL AI Assistant/file_list_cache.json`
- Acceleration: in-memory bigram/extension/drive inverted index
- Result shape today: `name`, `path`, optional `last_modified`

### Content/vector search

- Entry point: `tools.search_content()`
- Embedding: `rag_engine.get_embeddings()`
- Vector backend: `faiss_store.search_similar()`
- Storage: FAISS or TurboVec index plus `metadata.jsonl`
- Result shape today: `content`, `source`, `metadata`, `score`

## Recent Stability Guardrails

v1.2.5 added resource-runaway protection:

- `.dwg` is included in embeddable extensions.
- `$Recycle.Bin` remains included by default.
- VectorStore eager-load size guardrails run before index/metadata loading.
- VectorStore load failures use backoff and session disable after repeated failures.
- Processing filters run before VectorStore load.

## Docufinder-Inspired Roadmap

The following ideas are adopted from Docufinder as architecture patterns, not as a Rust/Tauri rewrite.

### Phase 1: normalized search results

Add a shared internal result contract so filename and content results can be ranked and rendered together. Existing public return dictionaries should remain backward-compatible.

### Phase 2: RRF hybrid search

Add Reciprocal Rank Fusion over existing filename search and vector search:

```text
filename results + vector results -> normalized results -> RRF -> hybrid results
```

No user-facing search operators are added.

### Phase 3: search mode separation

Use existing intent classification to avoid always doing file-first search. Clear file-finding requests can stay filename-focused; content requests can go straight to content search; ambiguous requests can use hybrid search.

### Phase 4: SQLite metadata + FTS5 sidecar

Add SQLite as a sidecar metadata and FTS5 index while keeping JSONL/vector storage authoritative at first. Current environment check passed:

```text
python sqlite3 FTS5: OK
```

SQLite should initially hold document/chunk/status metadata and support lexical search. It must not replace JSONL/FAISS until side-by-side validation passes.

### Phase 5: source evidence UX

Show stronger source cards: filename, path, snippet, score, source engine, page/sheet metadata where available, and open-file/open-folder actions.

### Phase 6: indexing status UI

Expose current file, processed/skipped/error counts, backoff state, disabled state, and extension statistics. Later, use SQLite `index_events` as the durable source.

## Non-Goals

- No Rust/Tauri rewrite.
- No usearch migration in the first iteration.
- No big-bang JSONL-to-SQLite migration.
- No user-facing query operators.
- No full PDF/Office renderer in the first preview iteration.
- No sharding before SQLite/FTS is proven stable.
