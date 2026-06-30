# -*- coding: utf-8 -*-
"""Centralized drive discovery and search-path policy."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, List

from config_manager import load_config


DEFAULT_DRIVES = ["X:/", "Z:/", "Y:/"]


def _normalize_root(path: str) -> str:
    root = str(path).replace("\\", "/").rstrip("/")
    return root + "/"


def _net_use_drives() -> List[str]:
    """Return mapped Windows network drive roots reported by ``net use``.

    ``os.listdrives()`` is usually enough, but mapped network drives can be
    missed depending on Windows session/elevation state. ``net use`` is the
    most direct source for user-mapped network drive letters, and it supports
    arbitrary letters rather than the historical X/Y/Z assumptions.
    """
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(
            ["net", "use"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    drives: List[str] = []
    for line in (result.stdout or "").splitlines():
        match = re.search(r"\b([A-Z]):\s+\\\\", line, re.IGNORECASE)
        if match:
            drives.append(f"{match.group(1).upper()}:/")
    return drives


def _windows_connected_drives() -> List[str]:
    drives: List[str] = []
    if hasattr(os, "listdrives"):
        try:
            drives = list(os.listdrives())
        except Exception:
            drives = []
    if not drives:
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:/"
            if os.path.exists(root):
                drives.append(root)
    drives.extend(_net_use_drives())
    return sorted(dict.fromkeys(_normalize_root(d) for d in drives))


def get_search_roots() -> List[str]:
    """Return currently searchable drive roots.

    Defaults to all connected Windows drives. If discovery fails, falls back to the
    historical X/Z/Y network drives that are currently connected.
    """
    config = load_config().get("search", {})
    mode = config.get("drive_mode", "connected")
    if mode == "manual":
        roots = config.get("manual_drives", DEFAULT_DRIVES)
    else:
        roots = _windows_connected_drives()
    if not roots:
        roots = DEFAULT_DRIVES
    return sorted(dict.fromkeys(_normalize_root(r) for r in roots))


def get_exclude_dir_names() -> set[str]:
    return {str(x).lower() for x in load_config().get("search", {}).get("exclude_dirs", [])}


def should_skip_dir(path: str, exclude_names: Iterable[str] | None = None) -> bool:
    names = set(exclude_names or get_exclude_dir_names())
    return Path(path).name.lower() in names


def filter_walk_dirs(dirnames: List[str], parent: str, exclude_names: Iterable[str] | None = None) -> None:
    """In-place filter for os.walk dirnames."""
    names = set(exclude_names or get_exclude_dir_names())
    dirnames[:] = [d for d in dirnames if d.lower() not in names]
