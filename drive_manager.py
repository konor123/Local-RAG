# -*- coding: utf-8 -*-
"""Centralized drive discovery and search-path policy."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List

from config_manager import load_config


DEFAULT_DRIVES = ["X:/", "Z:/", "Y:/"]


def _normalize_root(path: str) -> str:
    root = str(path).replace("\\", "/").rstrip("/")
    return root + "/"


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
    return [_normalize_root(d) for d in drives if os.path.exists(d)]


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
        roots = [d for d in DEFAULT_DRIVES if os.path.exists(d)]
    return sorted(dict.fromkeys(_normalize_root(r) for r in roots if os.path.exists(r)))


def get_exclude_dir_names() -> set[str]:
    return {str(x).lower() for x in load_config().get("search", {}).get("exclude_dirs", [])}


def should_skip_dir(path: str, exclude_names: Iterable[str] | None = None) -> bool:
    names = set(exclude_names or get_exclude_dir_names())
    return Path(path).name.lower() in names


def filter_walk_dirs(dirnames: List[str], parent: str, exclude_names: Iterable[str] | None = None) -> None:
    """In-place filter for os.walk dirnames."""
    names = set(exclude_names or get_exclude_dir_names())
    dirnames[:] = [d for d in dirnames if d.lower() not in names]
