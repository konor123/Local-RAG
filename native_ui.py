# -*- coding: utf-8 -*-
"""Minimal PySide6 native UI for OSL RAG Internal.

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
import traceback
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
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


APP_TITLE = "OSL RAG Internal"
FILE_URL_PREFIX = "file-oslref:///"


# ─── Tray icon ──────────────────────────────────────────
def _make_tray_icon(color: QColor = QColor(76, 175, 80)) -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(Qt.GlobalColor.white)
    font = painter.font()
    font.setBold(True)
    font.setPointSize(24)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "R")
    painter.end()
    return QIcon(pixmap)


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
            f'<pre style="background:#0f172a;color:#e2e8f0;padding:8px;border-radius:4px;'
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
        '<code style="background:#e2e8f0;padding:1px 4px;border-radius:3px">\\1</code>',
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
    return folder / "OSL RAG Internal.bat"


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
        self.embedder = BackgroundEmbedder(sleep_between_files=5.0, batch_size=10)
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
        self._cache_timer.start(60 * 60 * 1000)
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
        if not files:
            return
        self.embedder.start(files, status_callback=None)

    def cache_text(self) -> str:
        if self.cache_manager.is_refreshing():
            return "📊 캐시: 갱신 중..."
        return f"📊 캐시: {self.cache_manager.get_file_count():,} 파일"

    def embed_text(self) -> str:
        st = self.embedder.get_status()
        processed = st.get("processed_count", 0)
        skip = st.get("skip_count", 0)
        error = st.get("error_count", 0)
        current = st.get("current_file", "")
        if self.embedder.is_running():
            current = current[:18]
            return f"⚙️ 임베딩: {current} ({processed:,} OK / {skip:,} skip / {error:,} err)"
        total = st.get("total_processed", 0)
        return f"⚙️ 임베딩: 대기 (누적 {total:,} 처리)"


# ─── Chat window ───────────────────────────────────────
class ChatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(900, 720)

        self.bg = BackgroundTaskManager(self)
        self.preload_worker: PreloadWorker | None = None
        self.chat_worker: Optional[ChatWorker] = None
        self.chat_history: List[tuple] = []

        self._build_ui()
        self._build_tray()
        self._start_background()
        self._start_preload()

    # ─── UI ───────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

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
        show_action = QAction("OSL RAG 열기", self)
        show_action.triggered.connect(self.show_normal)
        menu.addAction(show_action)
        reset_action = QAction("🗑️ 대화 초기화", self)
        reset_action.triggered.connect(self.reset_chat)
        menu.addAction(reset_action)
        menu.addSeparator()
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

    def _start_background(self) -> None:
        self.bg.start()

    def _start_preload(self) -> None:
        self.preload_worker = PreloadWorker(self)
        self.preload_worker.status.connect(lambda text: self._append_log("info", text))
        self.preload_worker.finished_ok.connect(self._on_preload_done)
        self.preload_worker.start()

    def _on_preload_done(self, ok: bool, message: str) -> None:
        if ok:
            self._append_log("info", "모델 준비 완료. 질문을 입력하세요.")
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
            f'<div style="margin:10px 0"><b style="color:#0b5cad">사용자</b><br>{html_content}</div>'
        )

    def _append_assistant(self, content: str, sources: list) -> None:
        html_content = _markdown_to_html(content)
        html_parts = [f'<div style="margin:10px 0"><b style="color:#1b5e20">OSL RAG</b><br>{html_content}</div>']
        if sources:
            html_parts.append('<div style="margin:6px 0 10px 14px">')
            html_parts.append('<b style="color:#374151">📎 참조 파일</b><br>')
            for src in sources:
                path = src.get("source") or ""
                if not path:
                    continue
                href = ChatBrowser.encode_path(path)
                display = _escape(path)
                html_parts.append(
                    f'<a href="{href}" style="color:#3b82f6;text-decoration:underline">📂 {display}</a><br>'
                )
            html_parts.append("</div>")
        self.chat_view.append("".join(html_parts))

    def _append_log(self, kind: str, text: str) -> None:
        color = {
            "info": "#6b7280",
            "tool": "#6b7280",
            "error": "#dc2626",
        }.get(kind, "#6b7280")
        text_html = _escape(text).replace("\n", "<br>")
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
        self.tray.hide()
        QApplication.quit()


def main() -> int:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = ChatWindow()
    if not load_config().get("native_ui", {}).get("start_hidden", False):
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
