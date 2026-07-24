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
        "ocr": {
            "enabled": True,
            "auto_on_direct_read": True,
            "preload_on_startup": True,
            "direct_read_ocr_timeout_sec": 45,
            "direct_read_ocr_max_pages": 20,
        },
    },
    "embedding": {
        "enabled": True,
        "max_load_failures": 3,
        "retry_backoff_seconds": [60, 300, 900, 3600],
        "max_metadata_mb_for_eager_load": 512,
        "max_index_mb_for_eager_load": 2048,
        "adaptive_eager_load": {
            "enabled": True,
            "available_ram_fraction": 0.50,
            "minimum_system_reserve_mb": 4096,
            "minimum_system_reserve_fraction": 0.15,
            "metadata_ram_multiplier": 5.0,
            "index_ram_multiplier": 1.15,
            "embedding_model_reserve_mb": 768,
            "external_model_reserve_mb": 0,
            "transient_reserve_mb": 512,
            "metadata_cap_ceiling_mb": 1024,
        },
        "max_file_size_mb": 200,
    },
    "metadata_index": {
        "enabled": True,
        "fts_search_enabled": True,
        "path": "%LOCALAPPDATA%/OSL AI Assistant/metadata_index.sqlite3",
    },
    "atomization": {
        "enabled": False,
        "llm_enabled": False,
        "candidate_k": 20,
        "parent_k": 5,
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


def _enforce_internal_defaults(config: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """Apply required defaults and migrate retired local model settings."""
    changed = False
    metadata = config.setdefault("metadata_index", {})
    if metadata.get("enabled") is not True:
        metadata["enabled"] = True
        changed = True
    if metadata.get("fts_search_enabled") is not True:
        metadata["fts_search_enabled"] = True
        changed = True
    if not metadata.get("path"):
        metadata["path"] = DEFAULT_CONFIG["metadata_index"]["path"]
        changed = True

    search = config.setdefault("search", {})
    ocr_defaults = DEFAULT_CONFIG.get("search", {}).get("ocr", {})
    ocr_config = search.setdefault("ocr", {})
    for key, value in ocr_defaults.items():
        if key not in ocr_config:
            ocr_config[key] = value
            changed = True

    local = config.setdefault("ai_provider", {}).setdefault("local", {})
    retired_model = "cookieshake/a.x-4.0-light-imatrix:q4_k_m"
    retired_model_prefix = "cookieshake/a.x-4.0-light-imatrix"
    qwen_model = DEFAULT_CONFIG["ai_provider"]["local"]["adviser_model"]
    for key in ("adviser_model", "fallback_agent_model"):
        if local.get(key) == retired_model:
            local[key] = qwen_model
            changed = True
    available_models = local.get("available_models")
    if isinstance(available_models, list):
        filtered_models = [model for model in available_models if not str(model).startswith(retired_model_prefix)]
        if filtered_models != available_models:
            local["available_models"] = filtered_models
            changed = True
    capabilities = local.get("model_capabilities")
    if isinstance(capabilities, dict):
        retired_keys = [key for key in capabilities if str(key).startswith(retired_model_prefix)]
        if retired_keys:
            for key in retired_keys:
                del capabilities[key]
            changed = True
    return config, changed


def load_config() -> Dict[str, Any]:
    path = _config_path()
    if not path.exists():
        config, _ = _enforce_internal_defaults(deepcopy(DEFAULT_CONFIG))
        return config
    try:
        with path.open("r", encoding="utf-8") as f:
            merged = _merge_defaults(json.load(f), DEFAULT_CONFIG)
        merged, changed = _enforce_internal_defaults(merged)
        if changed:
            try:
                with path.open("w", encoding="utf-8") as out:
                    json.dump(merged, out, ensure_ascii=False, indent=2)
            except Exception as save_exc:
                print(f"[Config] Failed to persist v1.4.4 defaults: {save_exc}")
        return merged
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
