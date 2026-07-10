# -*- coding: utf-8 -*-
"""Minimal PySide6 native UI for OSL AI Assistant.

Layout: only chat thread + input box. All thinking/tool/reference events
render inline in the chat. Cache refresh and embedding run continuously in
the background; the tray exposes status only.
"""
from __future__ import annotations

import html
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

import requests
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSystemTrayIcon,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from background_embedder import BackgroundEmbedder
from cache_manager import CacheManager
from config_manager import load_config, save_config
from unified_engine import get_unified_response
from _version import __version__
from update_checker import check_for_update, download_and_prepare_update, launch_installer


APP_TITLE = "OSL AI Assistant"
FILE_URL_PREFIX = "file-oslref:///"
OLLAMA_URL = "http://127.0.0.1:11434"
_ollama_process: Optional[subprocess.Popen] = None
_SINGLE_INSTANCE_SERVER = "OSL_AI_Assistant_SingleInstance"


def _asset_path(filename: str) -> str:
    """Resolve an asset file path in both frozen (PyInstaller) and dev modes."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "assets" / filename)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "_internal" / "assets" / filename)
    candidates.append(Path(__file__).resolve().parent / "assets" / filename)
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[-1])


# ─── Ollama lifecycle ──────────────────────────────────
def _ollama_ready(timeout: float = 1.5) -> bool:
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def _find_ollama_executable() -> Optional[str]:
    candidates: List[Path] = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(getattr(sys, "_MEIPASS")) / "ollama" / "ollama.exe")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "ollama" / "ollama.exe")
    candidates.append(Path(__file__).resolve().parent / "ollama" / "ollama.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("ollama")


def _ensure_ollama_running() -> bool:
    """Start bundled Ollama only when no local server is already available."""
    global _ollama_process
    if _ollama_ready():
        return True
    exe = _find_ollama_executable()
    if not exe:
        raise RuntimeError("Ollama 실행 파일을 찾을 수 없습니다.")

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    _ollama_process = subprocess.Popen(
        [exe, "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
        close_fds=True,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if _ollama_ready(timeout=1.0):
            return True
        if _ollama_process.poll() is not None:
            if _ollama_ready(timeout=1.0):
                return True
            break
        time.sleep(0.75)
    raise RuntimeError("Ollama 서버가 시작되지 않았습니다. 포트 11434를 확인하세요.")


def _run_hidden(args: List[str], timeout: float = 8.0) -> int:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1


def _stop_ollama(force: bool = True, include_name_fallback: bool = False) -> None:
    """Stop Ollama owned by this app; optionally kill all Ollama for updates.

    Normal app quit only stops the process we launched. Update/install flows can
    request the image-name fallback because bundled files must be unlocked before
    the installer replaces them.
    """
    global _ollama_process
    owned_process = _ollama_process
    _ollama_process = None
    if owned_process is None and not include_name_fallback:
        return

    if owned_process is not None and owned_process.poll() is None:
        try:
            if os.name == "nt":
                args = ["taskkill", "/PID", str(owned_process.pid), "/T"]
                if force:
                    args.append("/F")
                _run_hidden(args)
            else:
                owned_process.terminate()
                try:
                    owned_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if force:
                        owned_process.kill()
        except Exception as exc:
            print(f"[Ollama] App-owned stop failed: {exc}")

    if include_name_fallback and os.name == "nt":
        for image in ("ollama.exe", "ollama_llama_server.exe", "llama-server.exe"):
            try:
                code = _run_hidden(["taskkill", "/F", "/T", "/IM", image])
                if code not in (0, 128):
                    print(f"[Ollama] taskkill {image} exited with {code}")
            except Exception as exc:
                print(f"[Ollama] taskkill {image} failed: {exc}")


# ─── Tray icon ──────────────────────────────────────────
def _make_tray_icon() -> QIcon:
    return QIcon(_asset_path("tray_icon.png"))


def _escape(text: str) -> str:
    return html.escape(str(text or ""))


def _markdown_to_html(text: str) -> str:
    """Convert lightweight Markdown to HTML for QTextBrowser display."""
    import re as _re

    if not text:
        return ""
    # Escape HTML first
    parts = _re.split(r"```([\s\S]*?)```", text)
    code_blocks: List[str] = []
    rendered: List[str] = []
    for idx, chunk in enumerate(parts):
        if idx % 2 == 1:
            code_blocks.append(chunk)
            rendered.append(f"\x00CODE{len(code_blocks) - 1}\x00")
        else:
            rendered.append(_escape(chunk))
    merged = "".join(rendered)
    # Code block placeholders
    for i, code in enumerate(code_blocks):
        merged = merged.replace(
            f"\x00CODE{i}\x00",
            f'<pre style="background:transparent;color:#cbd5e1;padding:8px;border-radius:4px;'
            f'border:1px solid #64748b;'
            f'overflow:auto;font-size:12px"><code>{_escape(code)}</code></pre>',
        )
    # Headings
    merged = _re.sub(r"(?m)^######\s*(.+)$", r"<h6>\1</h6>", merged)
    merged = _re.sub(r"(?m)^#####\s*(.+)$", r"<h5>\1</h5>", merged)
    merged = _re.sub(r"(?m)^####\s*(.+)$", r"<h4>\1</h4>", merged)
    merged = _re.sub(r"(?m)^###\s*(.+)$", r"<h3>\1</h3>", merged)
    merged = _re.sub(r"(?m)^##\s*(.+)$", r"<h2>\1</h2>", merged)
    merged = _re.sub(r"(?m)^#\s*(.+)$", r"<h1>\1</h1>", merged)
    # Blockquote
    merged = _re.sub(r"(?m)^&gt;\s?(.+)$",
        '<blockquote style="border-left:3px solid #94a3b8;margin:4px 0;padding:2px 8px;color:#475569">\\1</blockquote>',
        merged)
    # Horizontal rule
    merged = _re.sub(r"(?m)^---+\s*$", "<hr>", merged)
    # Bold / italic / inline code
    merged = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", merged)
    merged = _re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<i>\1</i>", merged)
    merged = _re.sub(r"`([^`]+)`",
        '<code style="background:transparent;color:#cbd5e1;padding:1px 4px;border-radius:3px">\\1</code>',
        merged)
    # Links: [text](url) — but only for non-file anchors to avoid conflicting with file:// rendering
    def _link_replace(match: "re.Match[str]") -> str:
        label = match.group(1)
        url = match.group(2)
        return f'<a href="{_escape(url)}">{label}</a>'

    merged = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link_replace, merged)
    # Bullet lists (- or *)
    merged = _re.sub(
        r"(?m)^(\s*)[-*]\s+(.+)$",
        r"\1• \2<br>",
        merged,
    )
    # Numbered lists
    merged = _re.sub(
        r"(?m)^(\s*)(\d+)\.\s+(.+)$",
        r"\1\2. \3<br>",
        merged,
    )
    # Newlines to <br> (avoiding ones inside headings/blocks/pres)
    merged = _re.sub(r"(?<!</pre>)\n", "<br>", merged)
    return merged


def _format_source_badge(source_engine: str) -> str:
    labels = {
        "filename": "파일명",
        "vector": "벡터",
        "sqlite_fts5": "FTS5",
        "hybrid_rrf": "하이브리드",
    }
    return labels.get(source_engine or "", source_engine or "unknown")


def _format_score(value) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return ""


def _metadata_summary(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return ""
    parts = []
    for key in ("page", "sheet", "file_type"):
        value = metadata.get(key)
        if value not in (None, ""):
            parts.append(f"{key}: {_escape(str(value))}")
    return " · ".join(parts)


def _source_card_html(src: dict) -> str:
    path = src.get("source") or ""
    if not path:
        return ""
    href = ChatBrowser.encode_path(path)
    filename = os.path.basename(path) or path
    engine = _format_source_badge(src.get("source_engine", src.get("type", "unknown")))
    score = _format_score(src.get("score"))
    metadata_text = _metadata_summary(src.get("metadata", {}))
    snippet = str(src.get("snippet") or "").strip().replace("\n", " ")[:180]
    detail_bits = [f"<span>{_escape(engine)}</span>"]
    if score:
        detail_bits.append(f"<span>score: {_escape(score)}</span>")
    if metadata_text:
        detail_bits.append(f"<span>{metadata_text}</span>")
    detail = " · ".join(detail_bits)
    detail_html = f'<div style="color:#94a3b8;font-size:11px;margin-top:2px">{detail}</div>' if len(detail_bits) > 1 else ""
    snippet_html = f'<div style="color:#cbd5e1;margin-top:4px;font-size:12px">{_escape(snippet)}</div>' if snippet else ""
    return (
        '<div style="border:1px solid #64748b;background:transparent;'
        'border-radius:6px;padding:8px;margin:6px 0">'
        f'<a href="{href}" style="color:#60a5fa;text-decoration:underline;font-weight:bold">📂 {_escape(filename)}</a>'
        f'{detail_html}'
        f'<div style="color:#94a3b8;font-size:11px;margin-top:2px">{_escape(path)}</div>'
        f'{snippet_html}'
        '</div>'
    )


# ─── File system helpers ───────────────────────────────
def _open_path(path: str) -> None:
    """Open a file or folder in the OS default handler."""
    if not path:
        return
    try:
        if platform.system() == "Windows":
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(os.path.normpath(path))
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        print(f"[OpenPath] Failed to open {path}: {exc}")


def _open_folder(path: str) -> None:
    """Open the containing folder of a file (or the path itself if dir)."""
    if not path:
        return
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if not folder:
        return
    if platform.system() == "Windows":
        try:
            os.startfile(os.path.normpath(folder))
            return
        except Exception:
            subprocess.Popen(["explorer", os.path.normpath(folder)])
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


# ─── Windows startup helper ────────────────────────────
def _startup_folder() -> Optional[Path]:
    """Return the user's Windows Startup folder (or None on other OS)."""
    if platform.system() != "Windows":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _startup_shortcut_path() -> Optional[Path]:
    folder = _startup_folder()
    if folder is None:
        return None
    return folder / "OSL_AI_Assistant.bat"


def _running_executable() -> str:
    """Best-effort target executable for the startup shortcut.

    PyInstaller-frozen binaries return sys.executable directly. When running
    as `python native_ui.py` during development, we point at `py -3.12
    native_ui.py` so the shortcut still launches the app.
    """
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return exe
    script_dir = Path(__file__).resolve().parent
    py_launcher = shutil.which("py")
    target_py = py_launcher or shutil.which("python") or exe
    script = script_dir / "native_ui.py"
    return f'"{target_py}" "{script}"'


def _enable_startup() -> bool:
    """Create a .bat launcher in the user's Startup folder. Returns success."""
    target = _startup_shortcut_path()
    if target is None:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        if getattr(sys, "frozen", False):
            target.write_text(
                f'@echo off\nstart "" "{sys.executable}"\n',
                encoding="utf-8",
            )
        else:
            target.write_text(
                f'@echo off\nstart "" {_running_executable()}\n',
                encoding="utf-8",
            )
        return True
    except Exception as exc:
        print(f"[Startup] Failed to enable: {exc}")
        return False


def _disable_startup() -> bool:
    target = _startup_shortcut_path()
    if target is None:
        return False
    try:
        if target.exists() or target.is_symlink():
            target.unlink()
        return True
    except Exception as exc:
        print(f"[Startup] Failed to disable: {exc}")
        return False


def _startup_enabled() -> bool:
    target = _startup_shortcut_path()
    return bool(target and (target.exists() or target.is_symlink()))


# ─── Chat browser with file path interactions ─────────
class ChatBrowser(QTextBrowser):
    file_anchor_clicked = Signal(str)
    file_anchor_right_clicked = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setReadOnly(True)
        self.setStyleSheet("font-size: 14px;")
        self._current_path: Optional[str] = None

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            anchor = self.anchorAt(event.position().toPoint())
            path = self._decode_anchor(anchor)
            if path:
                event.accept()
                _open_folder(path)
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802
        anchor = self.anchorAt(event.position().toPoint())
        path = self._decode_anchor(anchor)
        if path:
            menu = QMenu(self)
            open_file = QAction("📄 파일 열기", self)
            open_file.triggered.connect(lambda: _open_path(path))
            open_folder = QAction("📁 폴더 열기", self)
            open_folder.triggered.connect(lambda: _open_folder(path))
            copy_path = QAction("📋 경로 복사", self)
            copy_path.triggered.connect(lambda: QApplication.clipboard().setText(path))
            menu.addAction(open_file)
            menu.addAction(open_folder)
            menu.addSeparator()
            menu.addAction(copy_path)
            menu.exec(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    @staticmethod
    def encode_path(path: str) -> str:
        return FILE_URL_PREFIX + path.replace("\\", "/").lstrip("/")

    @staticmethod
    def _decode_anchor(anchor: str) -> Optional[str]:
        if not anchor:
            return None
        if anchor.startswith(FILE_URL_PREFIX):
            return anchor[len(FILE_URL_PREFIX):].lstrip("/")
        if anchor.startswith("file:///"):
            tail = anchor[len("file:///"):]
            if platform.system() == "Windows":
                tail = tail.lstrip("/")
            return tail
        return None


# ─── Workers ───────────────────────────────────────────
class PreloadWorker(QThread):
    status = Signal(str)
    finished_ok = Signal(bool, str)

    def run(self) -> None:
        ok = True
        messages: List[str] = []
        try:
            self.status.emit("Ollama 서버 상태 확인 중...")
            _ensure_ollama_running()
            self.status.emit("EXAONE 기본 모델 상태 확인 중...")
            from ai_providers.local_qwen import LocalQwenProvider

            provider = LocalQwenProvider()
            health = provider.health_check()
            if not health.get("ok"):
                ok = False
                messages.append(f"기본 모델 없음: {provider.model}")
            if not health.get("adviser_ok", True):
                ok = False
                messages.append(f"어드바이저 모델 없음: {provider.adviser_model}")
            self.status.emit("임베딩 모델 워밍업 중...")
            from rag_engine import get_embeddings

            embeddings = get_embeddings()
            embeddings.embed_query("워밍업")
            ocr_cfg = load_config().get("search", {}).get("ocr", {})
            if ocr_cfg.get("enabled", True) and ocr_cfg.get("preload_on_startup", True):
                try:
                    self.status.emit("OCR 엔진을 백그라운드에서 준비 중입니다...")
                    from ocr_utils import get_ocr

                    get_ocr()
                    self.status.emit("OCR 엔진 준비 완료")
                except Exception as ocr_exc:
                    messages.append(f"OCR 엔진 준비 실패: {ocr_exc}")
            self.status.emit("모델 준비 완료")
        except Exception as exc:
            ok = False
            messages.append(str(exc))
        self.finished_ok.emit(ok, "; ".join(messages) if messages else "ready")


class ChatWorker(QThread):
    event = Signal(dict)
    done = Signal(str, list, list)
    failed = Signal(str)

    def __init__(self, question: str, chat_history: List[tuple], parent=None):
        super().__init__(parent)
        self.question = question
        self.chat_history = chat_history

    def run(self) -> None:
        final_answer = ""
        sources: List[dict] = []
        tool_events: List[dict] = []
        try:
            for event in get_unified_response(self.question, self.chat_history):
                if event is None:
                    continue
                event_type = event.get("type")
                if event_type in ("tool_call", "tool_result"):
                    tool_events.append(event)
                elif event_type == "answer":
                    final_answer = event.get("content", "")
                    sources = event.get("sources", []) or []
                elif event_type == "error":
                    final_answer = f"오류: {event.get('content', '')}"
                self.event.emit(event)
            self.done.emit(final_answer, sources, tool_events)
        except Exception:
            self.failed.emit(traceback.format_exc())


class CacheRefreshWorker(QThread):
    finished_refresh = Signal()

    def __init__(self, cache_manager: CacheManager, parent=None):
        super().__init__(parent)
        self.cache_manager = cache_manager

    def run(self) -> None:
        try:
            self.cache_manager.refresh_cache(callback=None)
        except Exception as exc:
            print(f"[Background] Cache refresh error: {exc}")
        try:
            from tools import invalidate_file_cache
            invalidate_file_cache()
        except Exception:
            pass
        self.finished_refresh.emit()


class BackgroundTaskManager(QObject):
    """Run cache refresh and embedding continuously in the background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache_manager = CacheManager()
        self.embedder = BackgroundEmbedder(sleep_between_files=5.0, idle_sleep=60.0, batch_size=10)
        self._cache_timer: Optional[QTimer] = None
        self._refresh_thread: Optional[CacheRefreshWorker] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.refresh_cache()
        self._cache_timer = QTimer(self)
        self._cache_timer.timeout.connect(self.refresh_cache)
        # Keep cache refresh active so newly mounted/updated network drives are
        # discovered without user action.
        self._cache_timer.start(5 * 60 * 1000)
        self._start_embed_if_idle()

    def stop(self) -> None:
        self._running = False
        if self._cache_timer:
            self._cache_timer.stop()
        if self._refresh_thread and self._refresh_thread.isRunning():
            self._refresh_thread.quit()
            self._refresh_thread.wait(2000)
        self.embedder.stop()

    def refresh_cache(self) -> None:
        if self._refresh_thread and self._refresh_thread.isRunning():
            return
        self._refresh_thread = CacheRefreshWorker(self.cache_manager, self)
        self._refresh_thread.finished_refresh.connect(self._on_cache_refreshed)
        self._refresh_thread.start()

    def _on_cache_refreshed(self) -> None:
        self._refresh_thread = None
        self._start_embed_if_idle()

    def _start_embed_if_idle(self) -> None:
        if not self._running:
            return
        if self.embedder.is_running():
            return
        files = self.cache_manager.get_embeddable_files()
        self.embedder.start(files, status_callback=None, file_provider=self.cache_manager.get_embeddable_files)

    def cache_text(self) -> str:
        if self.cache_manager.is_refreshing():
            return "📊 캐시: 갱신 중..."
        return f"📊 캐시: {self.cache_manager.get_file_count():,} 파일"

    def embed_text(self) -> str:
        st = self.embedder.get_status()
        processed = st.get("processed_count", 0)
        skip = st.get("skip_count", 0)
        error = st.get("error_count", 0)
        total = st.get("total_processed", 0)
        state = st.get("state", "idle")
        current = (st.get("current_file", "") or "")[:18]
        processable_total = int(st.get("processable_total", 0) or 0)
        current_index = int(st.get("current_index", 0) or 0)

        if self.embedder.is_running():
            if state == "embedding" and processable_total:
                name = current or "처리 중"
                return f"⚙️ 임베딩 중: {current_index:,}/{processable_total:,} · {name}"
            if state == "loading":
                return "⚙️ 임베딩: 인덱스 준비 중..."
            if state == "backoff":
                return f"⚙️ 임베딩: 재시도 대기 중 · 오류 {error:,}"
            if state == "waiting_for_memory":
                wait = max(0, int(float(st.get("memory_wait_until", 0) or 0) - time.time()))
                return f"⚙️ 임베딩: 메모리 여유 대기 중 · {wait}초 후 재시도"
            if state == "disabled":
                return "⚙️ 임베딩: 비활성화됨"
            if state in ("idle", "scanning") and processable_total == 0:
                return f"⚙️ 임베딩: 모니터링 중 · 대기 파일 없음 (누적 {total:,})"
            label = current or "파일 확인 중"
            return f"⚙️ 임베딩: {label} · 대기 {processable_total:,}개"
        total = st.get("total_processed", 0)
        if state == "completed":
            return f"⚙️ 임베딩 완료: 성공 {processed:,}, 건너뜀 {skip:,}, 오류 {error:,}"
        if state == "disabled":
            return "⚙️ 임베딩: 비활성화됨"
        return f"⚙️ 임베딩: 대기 (누적 {total:,} 처리)"


class IndexingStatusDialog(QDialog):
    """Read-only snapshot of cache, embedding, SQLite, and OCR status."""

    def __init__(self, bg: BackgroundTaskManager, parent=None):
        super().__init__(parent)
        self.bg = bg
        self.setWindowTitle("인덱싱 상태")
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        self.text = QTextBrowser(self)
        self.text.setOpenExternalLinks(False)
        layout.addWidget(self.text, stretch=1)

        buttons = QHBoxLayout()
        refresh_btn = QPushButton("새로고침")
        refresh_btn.clicked.connect(self.refresh)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        buttons.addStretch(1)
        buttons.addWidget(refresh_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.text.setHtml(f"<pre style='font-family:Malgun Gothic, Consolas; font-size:12px'>{html.escape(self._snapshot_text())}</pre>")

    def _snapshot_text(self) -> str:
        st = self.bg.embedder.get_status()
        cfg = load_config()
        metadata = cfg.get("metadata_index", {})
        ocr_cfg = cfg.get("search", {}).get("ocr", {})
        backoff_until = float(st.get("backoff_until", 0) or 0)
        backoff_remaining = max(0, int(backoff_until - time.time())) if backoff_until else 0
        memory_wait_until = float(st.get("memory_wait_until", 0) or 0)
        memory_wait_remaining = max(0, int(memory_wait_until - time.time())) if memory_wait_until else 0
        memory = st.get("memory_wait_details", {}) or {}
        if not memory:
            try:
                from faiss_store import get_active_memory_load_diagnostics

                memory = get_active_memory_load_diagnostics()
            except Exception:
                memory = {}
        extension_stats = st.get("extension_stats", {}) or {}
        ext_lines = []
        for ext, stats in sorted(extension_stats.items()):
            if ext == "__total__":
                continue
            if isinstance(stats, dict):
                summary = ", ".join(f"{k}:{v}" for k, v in sorted(stats.items()))
                ext_lines.append(f"  - {ext}: {summary}")
        if not ext_lines:
            ext_lines.append("  - 아직 확장자별 통계가 없습니다.")

        lines = [
            "[캐시]",
            f"  상태: {self.bg.cache_text()}",
            f"  파일 수: {self.bg.cache_manager.get_file_count():,}",
            f"  갱신 중: {'예' if self.bg.cache_manager.is_refreshing() else '아니오'}",
            "",
            "[임베딩/인덱싱]",
            f"  요약: {self.bg.embed_text()}",
            f"  상태: {st.get('state', 'unknown')}",
            f"  현재 파일: {st.get('current_file') or '-'}",
            f"  진행률: {int(st.get('current_index', 0) or 0):,}/{int(st.get('processable_total', 0) or 0):,}",
            f"  남은 파일: {int(st.get('remaining_count', 0) or 0):,}",
            f"  성공/건너뜀/오류: {int(st.get('processed_count', 0) or 0):,} / {int(st.get('skip_count', 0) or 0):,} / {int(st.get('error_count', 0) or 0):,}",
            f"  누적 처리 파일: {int(st.get('total_processed', 0) or 0):,}",
            f"  비활성화: {'예' if st.get('embedding_disabled_for_session') else '아니오'}",
            f"  재시도 대기: {backoff_remaining}초" if backoff_remaining else "  재시도 대기: 없음",
            f"  메모리 대기: {memory_wait_remaining}초" if memory_wait_remaining else "  메모리 대기: 없음",
            f"  마지막 오류: {st.get('last_error') or '-'}",
            "",
            "[시스템 메모리/로드 판단]",
            f"  사용 가능 RAM: {memory.get('available_bytes', 0) / (1024 ** 3):.1f}GB" if memory else "  사용 가능 RAM: 확인 불가",
            f"  시스템 여유분: {memory.get('system_reserve_bytes', 0) / (1024 ** 3):.1f}GB" if memory else "  시스템 여유분: 확인 불가",
            f"  향후 작업 reserve: {memory.get('future_workload_reserve_bytes', 0) / (1024 ** 3):.1f}GB" if memory else "  향후 작업 reserve: 확인 불가",
            f"  안전 store 예산: {memory.get('store_budget_bytes', 0) / (1024 ** 3):.1f}GB" if memory else "  안전 store 예산: 확인 불가",
            f"  예상 store 필요량: {memory.get('estimated_store_bytes', 0) / (1024 ** 3):.1f}GB" if memory else "  예상 store 필요량: 확인 불가",
            f"  인덱스/메타데이터 크기: {memory.get('index_size_bytes', 0) / (1024 ** 2):.1f}MB / {memory.get('metadata_size_bytes', 0) / (1024 ** 2):.1f}MB" if memory else "  인덱스/메타데이터 크기: 확인 불가",
            f"  로드 판단: {'가능' if memory.get('allowed') else '메모리 확보 대기'}" if memory else "  로드 판단: 확인 불가",
            "",
            "[SQLite/FTS 인덱스]",
            f"  SQLite sidecar: {'사용' if metadata.get('enabled', True) else '미사용'}",
            f"  FTS 검색: {'사용' if metadata.get('fts_search_enabled', True) else '미사용'}",
            f"  경로: {metadata.get('path') or '-'}",
            "",
            "[OCR]",
            f"  직접 열람 OCR: {'사용' if ocr_cfg.get('enabled', True) and ocr_cfg.get('auto_on_direct_read', True) else '미사용'}",
            f"  시작 시 preload: {'사용' if ocr_cfg.get('preload_on_startup', True) else '미사용'}",
            f"  PDF당 제한: {ocr_cfg.get('direct_read_ocr_max_pages', 20)}페이지 / {ocr_cfg.get('direct_read_ocr_timeout_sec', 45)}초",
            "",
            "[확장자별 처리 통계]",
            *ext_lines,
        ]
        return "\n".join(lines)


# ─── Chat window ───────────────────────────────────────
class ChatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(_asset_path("app_icon.png")))
        self.resize(900, 720)

        self._instance_server: Optional[QLocalServer] = None
        self.bg = BackgroundTaskManager(self)
        self.preload_worker: PreloadWorker | None = None
        self.chat_worker: Optional[ChatWorker] = None
        self.chat_history: List[tuple] = []

        self._build_ui()
        self._build_tray()
        self._start_background()
        self._start_preload()
        QTimer.singleShot(3000, self._check_updates_on_startup)

    # ─── UI ───────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.version_label = QLabel(f"버전: v{__version__}")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.version_label.setStyleSheet("color:#6b7280;font-size:11px")
        layout.addWidget(self.version_label)

        self.chat_view = ChatBrowser()
        self.chat_view.anchorClicked.connect(lambda url: _open_folder(ChatBrowser._decode_anchor(url.toString()) or ""))
        layout.addWidget(self.chat_view, stretch=1)

        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("질문을 입력하세요... (Enter 전송)")
        self.input_box.returnPressed.connect(self.send_message)
        self.send_btn = QPushButton("전송")
        self.send_btn.clicked.connect(self.send_message)
        input_row.addWidget(self.input_box, stretch=1)
        input_row.addWidget(self.send_btn)
        layout.addLayout(input_row)

        self.setCentralWidget(central)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_tray_icon(), self)
        menu = QMenu()
        self._cache_action = QAction("📊 캐시: ...", self)
        self._cache_action.setEnabled(False)
        self._embed_action = QAction("⚙️ 임베딩: ...", self)
        self._embed_action.setEnabled(False)
        menu.addAction(self._cache_action)
        menu.addAction(self._embed_action)
        menu.addSeparator()
        show_action = QAction("OSL AI Assistant 열기", self)
        show_action.triggered.connect(self.show_normal)
        menu.addAction(show_action)
        reset_action = QAction("🗑️ 대화 초기화", self)
        reset_action.triggered.connect(self.reset_chat)
        menu.addAction(reset_action)
        update_action = QAction("업데이트 확인", self)
        update_action.triggered.connect(lambda: self.check_for_updates(silent=False))
        menu.addAction(update_action)
        menu.addSeparator()
        indexing_action = QAction("📊 인덱싱 상태", self)
        indexing_action.triggered.connect(self.show_indexing_status)
        menu.addAction(indexing_action)
        self._startup_action = QAction("시스템 시작 시 실행", self)
        self._startup_action.setCheckable(True)
        self._startup_action.setChecked(self._startup_initial_state())
        self._startup_action.toggled.connect(self._toggle_startup)
        menu.addAction(self._startup_action)
        menu.addSeparator()
        quit_action = QAction("❌ 종료", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_tray_status)
        self._status_timer.start(1000)
        self._update_tray_status()

    def _startup_initial_state(self) -> bool:
        """Read the start_with_system setting; ensure the file system matches it."""
        config = load_config()
        native = config.get("native_ui", {})
        configured = bool(native.get("start_with_system", True))
        on_disk = _startup_enabled()
        if configured and not on_disk:
            _enable_startup()
        elif not configured and on_disk:
            _disable_startup()
        return _startup_enabled()

    def _toggle_startup(self, checked: bool) -> None:
        if checked:
            ok = _enable_startup()
        else:
            ok = _disable_startup()
        if not ok:
            # Revert the menu state on failure
            self._startup_action.blockSignals(True)
            self._startup_action.setChecked(_startup_enabled())
            self._startup_action.blockSignals(False)
            return
        config = load_config()
        native = config.setdefault("native_ui", {})
        native["start_with_system"] = checked
        save_config(config)

    def _update_tray_status(self) -> None:
        if hasattr(self, "_cache_action"):
            self._cache_action.setText(self.bg.cache_text())
            self._embed_action.setText(self.bg.embed_text())
            self.tray.setToolTip(f"{self.bg.cache_text()}\n{self.bg.embed_text()}")

    def show_indexing_status(self) -> None:
        dialog = IndexingStatusDialog(self.bg, self)
        dialog.exec()

    def _check_updates_on_startup(self) -> None:
        auto_update = load_config().get("auto_update", {})
        if auto_update.get("enabled", True) and auto_update.get("check_on_startup", True):
            self.check_for_updates(silent=True)

    def check_for_updates(self, silent: bool = False) -> None:
        config = load_config()
        auto_update = config.setdefault("auto_update", {})
        try:
            update_info = check_for_update(
                __version__,
                skipped_version=auto_update.get("last_skipped_version"),
            )
            auto_update["last_check_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_config(config)
        except Exception as e:
            if not silent:
                QMessageBox.warning(self, "업데이트 확인 실패", f"업데이트 확인 중 오류가 발생했습니다.\n\n{e}")
            return

        if not update_info.get("update_available"):
            if not silent:
                QMessageBox.information(self, "업데이트 확인", "현재 최신 버전을 사용 중입니다.")
            return

        if silent:
            self.tray.showMessage(
                APP_TITLE,
                f"새 버전 {update_info.get('latest_tag')} 업데이트가 있습니다.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
        self._prompt_update(update_info)

    def _prompt_update(self, update_info: dict) -> None:
        size = update_info.get("size") or 0
        size_text = f"{size / (1024 * 1024):.1f} MB" if size else "알 수 없음"
        body = (update_info.get("body") or "").strip()
        if len(body) > 800:
            body = body[:800] + "..."

        box = QMessageBox(self)
        box.setWindowTitle("업데이트 가능")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"새 버전 {update_info.get('latest_tag')}이 있습니다.")
        box.setInformativeText(f"크기: {size_text}\n\n{body or '릴리즈 노트가 없습니다.'}")
        update_button = box.addButton("업데이트", QMessageBox.ButtonRole.AcceptRole)
        later_button = box.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        skip_button = box.addButton("이 버전 건너뛰기", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked == update_button:
            self._download_and_launch_update(update_info)
        elif clicked == skip_button:
            config = load_config()
            auto_update = config.setdefault("auto_update", {})
            auto_update["last_skipped_version"] = update_info.get("latest_tag")
            save_config(config)
        elif clicked == later_button:
            return

    def _download_and_launch_update(self, update_info: dict) -> None:
        progress = QProgressDialog("업데이트 다운로드 중...", "취소", 0, 100, self)
        progress.setWindowTitle("업데이트")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        def on_progress(downloaded: int, total: int) -> None:
            if total:
                progress.setValue(min(100, int(downloaded * 100 / total)))
            QApplication.processEvents()
            if progress.wasCanceled():
                raise RuntimeError("업데이트 다운로드가 취소되었습니다.")

        try:
            installer_path = download_and_prepare_update(update_info, on_progress)
            progress.setValue(100)
            self.bg.stop()
            _stop_ollama(force=True, include_name_fallback=True)
            launch_installer(installer_path)
            self.quit_app()
        except Exception as e:
            progress.close()
            QMessageBox.warning(self, "업데이트 실패", f"업데이트를 완료하지 못했습니다.\n\n{e}")

    def _start_background(self) -> None:
        self.bg.start()

    def _start_preload(self) -> None:
        self.preload_worker = PreloadWorker(self)
        self.preload_worker.status.connect(lambda text: self._append_log("info", text))
        self.preload_worker.finished_ok.connect(self._on_preload_done)
        self.preload_worker.start()

    def _on_preload_done(self, ok: bool, message: str) -> None:
        if ok:
            intro = (
                "OSL AI Assistant에 오신 것을 환영합니다.<br><br>"
                "이 프로그램은 여러분의 문서를 분석하고 질문에 답변하는 AI 비서입니다. "
                "백그라운드에서 학습이 진행될수록 더 정확한 답변을 제공합니다.<br><br>"
                "<b>이 프로그램은 인터넷에 연결되지 않으며, 문서 정보가 외부로 반출되지 않습니다.</b>"
            )
            self._append_log("intro", intro)
        else:
            self._append_log("error", f"모델 준비 경고: {message}")

    # ─── Chat events ──────────────────────────────────
    def send_message(self) -> None:
        question = self.input_box.text().strip()
        if not question or (self.chat_worker and self.chat_worker.isRunning()):
            return
        self.input_box.clear()
        self.send_btn.setEnabled(False)
        self._append_user(question)
        self.chat_worker = ChatWorker(question, self.chat_history.copy(), self)
        self.chat_worker.event.connect(self._handle_event)
        self.chat_worker.done.connect(lambda answer, sources, tools: self._handle_done(question, answer, sources, tools))
        self.chat_worker.failed.connect(self._handle_failed)
        self.chat_worker.start()

    def _handle_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "thinking":
            self._append_log("info", event.get("content", ""))
        elif kind == "tool_call":
            self._append_log("tool", f"🔍 {event.get('name')}: {event.get('args', {})}")
        elif kind == "tool_result":
            result = event.get("result", {})
            count = result.get("count", 0) if isinstance(result, dict) else 0
            self._append_log("tool", f"✅ {event.get('name')}: {count}개")
        elif kind == "error":
            self._append_log("error", f"오류: {event.get('content', '')}")

    def _handle_done(self, question: str, answer: str, sources: list, tools: list) -> None:
        self.send_btn.setEnabled(True)
        self._append_assistant(answer or "응답이 비어 있습니다.", sources or [])
        self.chat_history.append(("user", question))
        self.chat_history.append(("assistant", (answer or "")[:500]))

    def _handle_failed(self, detail: str) -> None:
        self.send_btn.setEnabled(True)
        self._append_assistant(f"오류가 발생했습니다:\n{detail}", [])

    def reset_chat(self) -> None:
        self.chat_history.clear()
        self.chat_view.clear()
        self._append_log("info", "대화를 초기화했습니다.")

    # ─── Renderers ────────────────────────────────────
    def _append_user(self, content: str) -> None:
        html_content = _escape(content).replace("\n", "<br>")
        self.chat_view.append(
            f'<div style="margin:10px 0"><b style="color:#60a5fa">사용자</b><br>{html_content}</div>'
        )

    def _append_assistant(self, content: str, sources: list) -> None:
        html_content = _markdown_to_html(content)
        html_parts = [f'<div style="margin:10px 0"><b style="color:#4ade80">OSL AI Assistant</b><br>{html_content}</div>']
        if sources:
            html_parts.append('<div style="margin:6px 0 10px 14px">')
            html_parts.append('<b style="color:#94a3b8">📎 참조 파일</b><br>')
            for src in sources:
                html_parts.append(_source_card_html(src))
            html_parts.append("</div>")
        self.chat_view.append("".join(html_parts))

    def _append_log(self, kind: str, text: str) -> None:
        color = {
            "info": "#6b7280",
            "tool": "#6b7280",
            "error": "#dc2626",
            "intro": "#ffffff",
        }.get(kind, "#6b7280")
        text_html = _escape(text).replace("\n", "<br>") if kind != "intro" else text
        self.chat_view.append(
            f'<div style="margin:2px 0;color:{color};font-size:12px">· {text_html}</div>'
        )

    # ─── Tray / window lifecycle ─────────────────────
    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_normal()

    def show_normal(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if load_config().get("native_ui", {}).get("minimize_to_tray", True):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                APP_TITLE,
                "트레이에 계속 실행 중입니다.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
        else:
            self.quit_app()

    def quit_app(self) -> None:
        self.bg.stop()
        _stop_ollama(force=True, include_name_fallback=False)
        self.tray.hide()
        QApplication.quit()


def _try_forward_to_existing_instance() -> bool:
    """Try connecting to an existing instance. Returns True if forwarded."""
    socket = QLocalSocket()
    socket.connectToServer(_SINGLE_INSTANCE_SERVER)
    if socket.waitForConnected(500):
        socket.write(b"show")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return True
    return False


def _create_single_instance_server() -> Optional[QLocalServer]:
    """Create a local server for single instance enforcement."""
    # Remove stale server if previous crash left it behind
    QLocalServer.removeServer(_SINGLE_INSTANCE_SERVER)
    server = QLocalServer()
    if not server.listen(_SINGLE_INSTANCE_SERVER):
        return None
    return server


def main() -> int:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(_asset_path("app_icon.png")))

    # ── Single instance guard ──────────────────────────────
    if _try_forward_to_existing_instance():
        return 0

    server = _create_single_instance_server()
    if server is None:
        return 0

    window = ChatWindow()
    window._instance_server = server
    server.newConnection.connect(lambda: _handle_new_connection(window))

    if not load_config().get("native_ui", {}).get("start_hidden", False):
        window.show()
    return app.exec()


def _handle_new_connection(window: "ChatWindow") -> None:
    """Bring the existing window to front when a second instance launches."""
    server = window._instance_server
    if server is None:
        return
    while server.hasPendingConnections():
        socket = server.nextPendingConnection()
        socket.waitForReadyRead(500)
        data = socket.readAll().data()
        if data == b"show":
            window.show_normal()
        socket.disconnectFromServer()


if __name__ == "__main__":
    raise SystemExit(main())
