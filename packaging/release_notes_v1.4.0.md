## v1.4.0

### Architecture
- Complete the SQLite/FTS5 sidecar validation path with bulk ingest dual-writes.
- Add SQLite sidecar diagnostics helpers for table counts and WAL checkpointing.
- Add a side-by-side validation CLI for comparing vector and SQLite FTS retrieval.

### Search
- Keep SQLite FTS behind explicit config flags while allowing hybrid RRF to include FTS results.
- Add tray toggles for SQLite sidecar and FTS search enablement.

### Evidence UX
- Preserve source engine, score, metadata, and snippet evidence through answer synthesis.
- Render source references as richer cards with engine labels, score, page/sheet metadata, and snippets.

### Maintenance
- Ignore local build logs and scratch test files to keep release commits clean.
