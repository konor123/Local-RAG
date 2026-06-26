"""Runtime data paths for the installed desktop app.

The application is installed under Program Files, which is not writable for
normal users. All caches, logs, trackers, and vector indexes must live under
the user's LocalAppData directory instead.
"""
from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "OSL RAG Internal"


def runtime_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_path(*parts: str) -> str:
    path = runtime_root().joinpath(*parts)
    if path.suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
    return str(path)


def logs_dir() -> str:
    return runtime_path("logs")
