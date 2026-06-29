# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OSL AI Assistant native UI.

Builds a single-folder distribution containing ``native_ui.exe`` and the
Python runtime with all required packages. Run from the project root:

    py -3.12 -m PyInstaller packaging/osl_rag.spec
"""
import os
import sys
from pathlib import Path

try:
    from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules
except Exception:
    collect_all = collect_data_files = collect_dynamic_libs = collect_submodules = None

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
SENTENCE_TRANSFORMERS_DATAS = []
SENTENCE_TRANSFORMERS_BINARIES = []
SENTENCE_TRANSFORMERS_HIDDENIMPORTS = []
if collect_all is not None:
    try:
        (
            SENTENCE_TRANSFORMERS_DATAS,
            SENTENCE_TRANSFORMERS_BINARIES,
            SENTENCE_TRANSFORMERS_HIDDENIMPORTS,
        ) = collect_all("sentence_transformers")
    except Exception:
        SENTENCE_TRANSFORMERS_HIDDENIMPORTS = _collect_optional("sentence_transformers", collect_submodules)
else:
    SENTENCE_TRANSFORMERS_HIDDENIMPORTS = _collect_optional("sentence_transformers", collect_submodules)

# Hard dependencies of sentence_transformers that PyInstaller's static
# analysis fails to collect (imported from SentenceTransformer.py at the top
# level). Without these, ``import sentence_transformers`` raises
# ``ModuleNotFoundError`` in the frozen environment.
HF_HUB_DATAS = []
HF_HUB_BINARIES = []
HF_HUB_HIDDENIMPORTS = []
if collect_all is not None:
    try:
        (
            HF_HUB_DATAS,
            HF_HUB_BINARIES,
            HF_HUB_HIDDENIMPORTS,
        ) = collect_all("huggingface_hub")
    except Exception:
        HF_HUB_HIDDENIMPORTS = _collect_optional("huggingface_hub", collect_submodules)
else:
    HF_HUB_HIDDENIMPORTS = _collect_optional("huggingface_hub", collect_submodules)

TQDM_DATAS = []
TQDM_BINARIES = []
TQDM_HIDDENIMPORTS = []
if collect_all is not None:
    try:
        (
            TQDM_DATAS,
            TQDM_BINARIES,
            TQDM_HIDDENIMPORTS,
        ) = collect_all("tqdm")
    except Exception:
        TQDM_HIDDENIMPORTS = _collect_optional("tqdm", collect_submodules)
else:
    TQDM_HIDDENIMPORTS = _collect_optional("tqdm", collect_submodules)

# scipy is required by sentence_transformers submodules
# (scipy.sparse, scipy.stats). Use collect_all to ensure the main
# __init__.py is included; otherwise PyInstaller collects submodules
# but not the package init and ``import scipy`` fails in the frozen env.
SCIPY_DATAS = []
SCIPY_BINARIES = []
SCIPY_HIDDENIMPORTS = []
if collect_all is not None:
    try:
        (
            SCIPY_DATAS,
            SCIPY_BINARIES,
            SCIPY_HIDDENIMPORTS,
        ) = collect_all("scipy")
    except Exception:
        SCIPY_HIDDENIMPORTS = _collect_optional("scipy", collect_submodules)
else:
    SCIPY_HIDDENIMPORTS = _collect_optional("scipy", collect_submodules)

# Entry point
a = Analysis(
    [str(PROJECT_ROOT / "native_ui.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=PADDLE_BINARIES + SENTENCE_TRANSFORMERS_BINARIES + HF_HUB_BINARIES + TQDM_BINARIES + SCIPY_BINARIES,
    datas=[
        # None of the runtime data (cache, embeddings, logs) is shipped; the
        # installer copies them in.
    ] + PADDLE_DATAS + SENTENCE_TRANSFORMERS_DATAS + HF_HUB_DATAS + TQDM_DATAS + SCIPY_DATAS,
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
        "sentence_transformers",
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
    ] + PADDLE_HIDDENIMPORTS + SENTENCE_TRANSFORMERS_HIDDENIMPORTS + HF_HUB_HIDDENIMPORTS + TQDM_HIDDENIMPORTS + SCIPY_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        # "scipy" is a hard dependency of sentence_transformers
        # (scipy.sparse, scipy.stats are imported by submodules).
        # Removing it from excludes so the package is bundled.
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
