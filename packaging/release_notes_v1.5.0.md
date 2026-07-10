# OSL AI Assistant v1.5.0

## Highlights

- TurboVec metadata now uses SQLite vector-id lookup instead of loading `metadata.jsonl` into memory.
- Reduces startup/search memory pressure for large indexes and avoids multi-GB JSONL expansion.
- Installer now checks `Windows 시작 시 자동 실행` by default.

## Indexing / Search

- TurboVec stores vectors in the vector index and metadata in SQLite.
- Search retrieves top-k vector IDs first, then fetches only matching metadata rows from SQLite.
- Legacy or incomplete TurboVec stores without SQLite vector metadata now fail closed with reindex guidance.
- Active TurboVec memory diagnostics no longer count legacy metadata JSONL size.

## Reliability

- Added serialized SQLite vector-id allocation with `BEGIN IMMEDIATE` and busy timeout.
- Added allocator recovery so new IDs advance beyond existing vector metadata rows.
- Prevented legacy sidecar upserts from overwriting TurboVec vector metadata.

## Upgrade Note

Existing FAISS/TurboVec metadata JSONL indexes should be rebuilt for this release. Back up or reset old index files and processed-file trackers before starting the first full reindex.