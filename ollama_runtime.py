"""Bounded, observable lifecycle management for the local Ollama server."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse

import requests


def normalize_base_url(value: str) -> str:
    """Return a safe HTTP(S) Ollama base URL without a trailing slash."""
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Ollama 서버 주소는 http(s)://호스트[:포트] 형식이어야 합니다.")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def find_ollama_executable() -> Optional[str]:
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(getattr(sys, "_MEIPASS")) / "ollama" / "ollama.exe")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "ollama" / "ollama.exe")
    candidates.append(Path(__file__).resolve().parent / "ollama" / "ollama.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("ollama")


@dataclass(frozen=True)
class OllamaDiagnostic:
    category: str
    endpoint: str
    detail: str = ""
    executable: str = ""
    exit_code: Optional[int] = None
    elapsed_seconds: float = 0.0
    output_tail: str = ""

    def user_message(self) -> str:
        messages = {
            "invalid_base_url": "Ollama 서버 주소 설정이 올바르지 않습니다.",
            "executable_not_found": "Ollama 실행 파일을 찾을 수 없습니다.",
            "launch_failed": "Ollama 서버를 실행하지 못했습니다.",
            "child_exited": "Ollama 서버가 시작 중 종료되었습니다.",
            "endpoint_not_ollama": "설정된 주소가 Ollama 서버로 응답하지 않습니다.",
            "readiness_timeout": "Ollama 서버가 제한 시간 안에 준비되지 않았습니다.",
        }
        suffix = f" 주소: {self.endpoint}" if self.endpoint else ""
        if self.exit_code is not None:
            suffix += f" (종료 코드: {self.exit_code})"
        return messages.get(self.category, "Ollama 서버 상태를 확인하지 못했습니다.") + suffix


class OllamaStartupError(RuntimeError):
    def __init__(self, diagnostic: OllamaDiagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic.user_message())


class OllamaRuntimeSupervisor:
    """Start at most one app-owned server and wait for its configured endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        executable_resolver: Callable[[], Optional[str]] = find_ollama_executable,
        probe: Optional[Callable[[str, float], tuple[bool, str]]] = None,
        launcher: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        startup_timeout: float = 30.0,
        handoff_grace: float = 5.0,
        log_path: Optional[Path] = None,
    ):
        self.base_url = normalize_base_url(base_url)
        self._executable_resolver = executable_resolver
        self._probe = probe or self._http_probe
        self._launcher = launcher
        self._clock = clock
        self._sleeper = sleeper
        self._startup_timeout = max(1.0, startup_timeout)
        self._handoff_grace = max(0.0, handoff_grace)
        self._log_path = log_path or Path(tempfile.gettempdir()) / "osl_ollama_startup.log"
        self._log_handle = None
        self.owned_process: Optional[subprocess.Popen] = None

    @staticmethod
    def _http_probe(base_url: str, timeout: float) -> tuple[bool, str]:
        try:
            response = requests.get(f"{base_url}/api/tags", timeout=timeout)
            if response.status_code == 200:
                return True, ""
            return False, f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            return False, type(exc).__name__

    def is_ready(self, timeout: float = 1.5) -> bool:
        return self._probe(self.base_url, timeout)[0]

    def ensure_running(self) -> str:
        if self.is_ready():
            return "already_running"
        executable = self._executable_resolver()
        if not executable:
            self._raise("executable_not_found")
        started = self._clock()
        try:
            self._prepare_log()
            self.owned_process = self._launch(executable)
            self._close_log()
        except OSError as exc:
            self._close_log()
            self._raise("launch_failed", detail=type(exc).__name__, executable=executable, started=started)
        delay = 0.25
        handoff_deadline: Optional[float] = None
        last_detail = ""
        while self._clock() - started < self._startup_timeout:
            ready, detail = self._probe(self.base_url, min(1.5, delay + 0.5))
            if ready:
                if self.owned_process is not None and self.owned_process.poll() is not None:
                    self.owned_process = None
                    return "delegated_external"
                return "started_owned"
            last_detail = detail
            if self.owned_process is not None and self.owned_process.poll() is not None:
                if handoff_deadline is None:
                    handoff_deadline = min(started + self._startup_timeout, self._clock() + self._handoff_grace)
                if self._clock() >= handoff_deadline:
                    code = self.owned_process.poll()
                    self.owned_process = None
                    self._raise("child_exited", detail=last_detail, executable=executable, exit_code=code, started=started)
            self._sleeper(delay)
            delay = min(1.5, delay * 1.75)
        category = "endpoint_not_ollama" if last_detail.startswith("HTTP ") else "readiness_timeout"
        self._raise(category, detail=last_detail, executable=executable, started=started)

    def _launch(self, executable: str) -> subprocess.Popen:
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": self._log_handle, "stderr": self._log_handle, "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
        return self._launcher([executable, "serve"], **kwargs)

    def _prepare_log(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self._log_path.open("w", encoding="utf-8")

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _tail(self) -> str:
        try:
            with self._log_path.open("r", encoding="utf-8", errors="replace") as log:
                return re.sub(r"//[^/@\s]+@", "//***@", log.read()[-2000:]).strip()
        except (AttributeError, OSError):
            return ""

    def _raise(self, category: str, detail: str = "", executable: str = "", exit_code: Optional[int] = None, started: Optional[float] = None) -> None:
        elapsed = self._clock() - started if started is not None else 0.0
        raise OllamaStartupError(OllamaDiagnostic(category, self.base_url, detail, executable or "", exit_code, elapsed, self._tail()))
