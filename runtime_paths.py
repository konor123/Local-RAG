"""Runtime data paths for the installed desktop app.

The application is installed under Program Files, which is not writable for
normal users. All caches, logs, trackers, and vector indexes must live under
the user's LocalAppData directory instead.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


OLD_APP_NAME = "OSL RAG Internal"
APP_NAME = "OSL AI Assistant"
_MIGRATION_SENTINEL = ".migrated_from_v110"


def _migrate_legacy_runtime_path(new_path: Path) -> None:
    """One-time migration from the legacy ``OSL RAG Internal`` runtime dir.

    Moves the legacy LocalAppData directory into the new ``OSL AI Assistant``
    path on first run, so existing users keep their caches, logs, and vector
    indexes. The legacy directory is left behind (but empty) when the move is
    not possible (e.g. cross-device) so the user can still recover data.
    """
    if new_path.exists():
        return
    base = new_path.parent
    legacy = base / OLD_APP_NAME
    if not legacy.exists() or not legacy.is_dir():
        return
    sentinel = new_path / _MIGRATION_SENTINEL
    if sentinel.exists():
        return
    new_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(legacy), str(new_path))
        # Recreate the sentinel so a future rename of the same base does not
        # silently re-trigger the move.
        new_path.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            f"Migrated from {legacy} to {new_path}\n",
            encoding="utf-8",
        )
    except OSError:
        # If the move fails (cross-device, locked file, etc.), fall back to a
        # best-effort copy so the new install still has a working directory.
        try:
            shutil.copytree(str(legacy), str(new_path))
        except OSError:
            new_path.mkdir(parents=True, exist_ok=True)


def runtime_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    new_path = Path(base) / APP_NAME
    _migrate_legacy_runtime_path(new_path)
    new_path.mkdir(parents=True, exist_ok=True)
    return new_path


def runtime_path(*parts: str) -> str:
    path = runtime_root().joinpath(*parts)
    if path.suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
    return str(path)


def logs_dir() -> str:
    return runtime_path("logs")
