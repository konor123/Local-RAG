# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OSL RAG Internal native UI.

Builds a single-folder distribution containing ``native_ui.exe`` and the
Python runtime with all required packages. Run from the project root:

    py -3.12 -m PyInstaller packaging/osl_rag.spec
"""
import os
import sys
from pathlib import Path

try:
    from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
except Exception:
    collect_data_files = collect_dynamic_libs = collect_submodules = None

def _collect_optional(package_name, collector):
    if collector is None:
        return []
    try:
        return collector(package_name)
    except Exception:
        return []

PROJECT_ROOT = Path(os.path.abspath(SPECPATH)).parent
PADDLE_HIDDENIMPORTS = [
    "paddle",
    "paddleocr",
    "pypdfium2",
    "pypdfium2_raw",
]
PADDLE_HIDDENIMPORTS += _collect_optional("paddle", collect_submodules)
PADDLE_HIDDENIMPORTS += _collect_optional("paddleocr", collect_submodules)
PADDLE_DATAS = _collect_optional("paddleocr", collect_data_files)
PADDLE_BINARIES = _collect_optional("paddle", collect_dynamic_libs)
PADDLE_BINARIES += _collect_optional("pypdfium2_raw", collect_dynamic_libs)

# Entry point
a = Analysis(
    [str(PROJECT_ROOT / "native_ui.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=PADDLE_BINARIES,
    datas=[
        # None of the runtime data (cache, embeddings, logs) is shipped; the
        # installer copies them in.
    ] + PADDLE_DATAS,
    hiddenimports=[
        "langchain_community.document_loaders",
        "langchain_community.embeddings",
        "langchain_community.vectorstores",
        "langchain_text_splitters",
        "langchain_ollama",
        "langchain.chains",
        "langchain.prompts",
        "ai_providers.local_qwen",
        "ai_providers.provider_manager",
        "_version",
        "update_checker",
        "turbovec",
        "faiss",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "chardet",
        "unstructured",
        "hwpkit",
        "hwpkit.hwpx",
        "pptx",
    ] + PADDLE_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="native_ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="native_ui",
)
