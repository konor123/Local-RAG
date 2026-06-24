# -*- coding: utf-8 -*-
from __future__ import annotations

from config_manager import load_config
from ai_providers.local_qwen import LocalQwenProvider

_provider = None


def get_provider():
    global _provider
    if _provider is None:
        config = load_config()
        mode = config.get("ai_provider", {}).get("mode", "")
        if mode != "local":
            raise ValueError(
                f"Only local Ollama mode is supported in this build. "
                f"Configured ai_provider.mode={mode!r}. "
                f"Set 'mode' to 'local' in config.json."
            )
        _provider = LocalQwenProvider()
    return _provider


def reset_provider():
    global _provider
    _provider = None
