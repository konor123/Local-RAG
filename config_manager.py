# -*- coding: utf-8 -*-
"""User configuration for the OSL AI Assistant local build."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


from runtime_paths import APP_NAME as APP_NAME  # re-exported for back-compat


OLD_APP_NAME = "OSL RAG Internal"

DEFAULT_CONFIG: Dict[str, Any] = {
    "ai_provider": {
        "mode": "local",
        "local": {
            "backend": "ollama",
            "default_model": "exaone3.5:2.4b",
            "selected_model": "exaone3.5:2.4b",
            "available_models": [
                "exaone3.5:2.4b",
                "qwen3.5:4b",
            ],
            "manual_agent_models": ["exaone3.5", "exaone3.5:2.4b"],
            "adviser_model": "qwen3.5:4b",
            "fallback_agent_model": "qwen3.5:4b",
            "model_capabilities": {
                "exaone3.5": {"tools": False},
                "exaone3.5:2.4b": {"tools": False},
                "qwen3.5": {"tools": True},
                "qwen3.5:4b": {"tools": True},
            },
            "base_url": "http://localhost:11434",
            "num_ctx": 4096,
            "num_predict": 512,
            "request_timeout": 180,
        },
        "cloud": {"enabled": False},
    },
    "native_ui": {"start_hidden": False, "minimize_to_tray": True, "start_with_system": True},
    "auto_update": {
        "enabled": True,
        "check_on_startup": True,
        "last_check_time": None,
        "last_skipped_version": None,
    },
    "search": {
        "drive_mode": "connected",
        "include_local_drives": True,
        "exclude_dirs": [
            "System Volume Information",
            "Windows",
            "Program Files",
            "Program Files (x86)",
            "AppData",
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
        ],
        "file_first": True,
        "file_search_sufficient_count": 1,
        "max_jit_files": 5,
    },
    "embedding": {
        "enabled": True,
        "max_load_failures": 3,
        "retry_backoff_seconds": [60, 300, 900, 3600],
        "max_metadata_mb_for_eager_load": 512,
        "max_index_mb_for_eager_load": 2048,
        "max_file_size_mb": 200,
    },
    "vector": {
        "backend": "turbovec",
        "index_dir": "%LOCALAPPDATA%/OSL AI Assistant/turbovec_index",
        "processed_files_path": "%LOCALAPPDATA%/OSL AI Assistant/processed_files_turbovec.txt",
    },
}


def _migrate_legacy_appdata_dir(new_path: Path) -> None:
    """Move a legacy ``%APPDATA%/OSL RAG Internal`` directory to the new path
    on first run so existing users keep their ``config.json`` and other Roaming
    data. Best-effort; failures are swallowed and the new path is still
    created so the app can start.
    """
    import shutil

    if new_path.exists():
        return
    base = new_path.parent
    legacy = base / OLD_APP_NAME
    if not legacy.exists() or not legacy.is_dir():
        return
    new_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(legacy), str(new_path))
    except OSError:
        try:
            shutil.copytree(str(legacy), str(new_path))
        except OSError:
            new_path.mkdir(parents=True, exist_ok=True)


def _appdata_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    new_path = Path(base) / APP_NAME
    _migrate_legacy_appdata_dir(new_path)
    new_path.mkdir(parents=True, exist_ok=True)
    return new_path


def _config_path() -> Path:
    return _appdata_dir() / "config.json"


def _merge_defaults(user_config: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in (user_config or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(value, merged[key])
        else:
            merged[key] = value
    return merged


def load_config() -> Dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return deepcopy(DEFAULT_CONFIG)
    try:
        with path.open("r", encoding="utf-8") as f:
            return _merge_defaults(json.load(f), DEFAULT_CONFIG)
    except Exception as exc:
        print(f"[Config] Failed to load config, using defaults: {exc}")
        return deepcopy(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _int_env(key: str, default: Any) -> int:
    val = os.environ.get(key)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    return int(default) if default is not None else default


def get_local_ai_config() -> Dict[str, Any]:
    config = load_config()
    local = config.get("ai_provider", {}).get("local", {})
    defaults = DEFAULT_CONFIG.get("ai_provider", {}).get("local", {})
    return {
        "model": os.environ.get("OSL_RAG_LOCAL_MODEL") or local.get("selected_model") or local.get("default_model", "exaone3.5:2.4b"),
        "base_url": os.environ.get("OSL_RAG_OLLAMA_BASE_URL") or local.get("base_url", "http://localhost:11434"),
        "available_models": local.get("available_models", defaults.get("available_models", [])),
        "manual_agent_models": local.get("manual_agent_models", defaults.get("manual_agent_models", [])),
        "fallback_agent_model": os.environ.get("OSL_RAG_AGENT_MODEL") or local.get("fallback_agent_model", defaults.get("fallback_agent_model")),
        "adviser_model": os.environ.get("OSL_RAG_ADVISER_MODEL") or local.get("adviser_model", defaults.get("adviser_model")),
        "model_capabilities": local.get("model_capabilities", defaults.get("model_capabilities", {})),
        "num_ctx": _int_env("OSL_RAG_NUM_CTX", local.get("num_ctx", defaults.get("num_ctx", 4096))),
        "num_predict": _int_env("OSL_RAG_NUM_PREDICT", local.get("num_predict", defaults.get("num_predict", 512))),
        "request_timeout": _int_env("OSL_RAG_REQUEST_TIMEOUT", local.get("request_timeout", defaults.get("request_timeout", 180))),
    }
