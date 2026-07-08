## v1.3.1

### Fixes
- Corrected embedding tray/status wording so idle monitoring no longer appears as `0/0/0` progress.
- Added explicit embedding queue state fields for current file, current index, pending count, backoff, and disabled states.
- Force-stop Ollama during update/install/uninstall flows to avoid locked bundled binaries.

### Architecture
- Added an opt-in SQLite/FTS5 metadata sidecar skeleton for Phase 4 validation.
- SQLite sidecar writes are best-effort and do not replace JSONL/vector storage yet.
