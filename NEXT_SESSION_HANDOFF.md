# NEXT SESSION HANDOFF

## Current State

- v1.3.1 GitHub Release is complete.
- `main` has advanced beyond v1.3.1 with Phase 4 SQLite/FTS work.
- Current target for the next update is v1.4.0.

## Completed for v1.4.0 Prep

- Version files bumped to 1.4.0.
- SQLite sidecar has stats and WAL checkpoint helpers.
- Bulk ingest mirrors status/chunk writes to SQLite sidecar via best-effort safe wrappers.
- Hybrid search can include SQLite FTS results when config flags are enabled.
- Source evidence metadata is preserved through answer synthesis.
- Native UI source references render as richer evidence cards.
- Tray menu includes toggles for SQLite sidecar and FTS search.
- Validation CLI added at `scripts/validate_sidecar.py`.

## Verification To Run

- `python -m py_compile ...` for changed Python files.
- `python -m unittest discover -s tests`.
- Manual PySide6 runtime check for source cards and tray toggles.
- Optional: `python scripts/validate_sidecar.py --query "유도등 설치 기준"` after enabling sidecar and indexing data.

## Release Checklist

- Confirm tests and reviewer pass.
- Build installer with Inno Setup/ISCC.
- Generate SHA256 after successful build.
- Tag `v1.4.0`.
- Create GitHub Release with installer and SHA256 assets.

## Do Not Commit

- Local build logs under `packaging/*.log`.
- Scratch files such as `test*.txt`.
- Local analysis reports unless explicitly requested.
