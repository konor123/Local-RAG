"""Semi-automatic GitHub Release updater for OSL AI Assistant."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import requests


GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/konor123/Local-RAG/releases/latest"
INSTALLER_SUFFIX = ".exe"
SHA256_SUFFIX = ".sha256.txt"

HTTP_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "OSL-AI-Assistant-Updater/1.2.4",
}


def parse_semver(version: str) -> Tuple[int, int, int]:
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", version or "")
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def is_newer_version(latest: str, current: str) -> bool:
    return parse_semver(latest) > parse_semver(current)


def _asset_download_url(asset: Dict) -> str:
    return asset.get("browser_download_url") or asset.get("url") or ""


def find_release_asset(release: Dict, suffix: str) -> Optional[Dict]:
    assets = release.get("assets") or []
    for asset in assets:
        name = (asset.get("name") or "").lower()
        if name.endswith(suffix):
            return asset
    return None


def get_latest_release(timeout: float = 10.0) -> Dict:
    response = requests.get(GITHUB_LATEST_RELEASE_URL, timeout=timeout, headers=HTTP_HEADERS)
    response.raise_for_status()
    return response.json()


def check_for_update(current_version: str, skipped_version: Optional[str] = None) -> Dict:
    release = get_latest_release()
    latest_tag = release.get("tag_name") or ""
    installer_asset = find_release_asset(release, INSTALLER_SUFFIX)
    sha_asset = find_release_asset(release, SHA256_SUFFIX)
    update_available = bool(
        latest_tag
        and is_newer_version(latest_tag, current_version)
        and latest_tag != skipped_version
        and installer_asset
    )
    return {
        "update_available": update_available,
        "latest_tag": latest_tag,
        "release_name": release.get("name") or latest_tag,
        "body": release.get("body") or "",
        "html_url": release.get("html_url") or "",
        "installer_asset": installer_asset,
        "sha_asset": sha_asset,
        "size": installer_asset.get("size") if installer_asset else None,
    }


def download_file(url: str, destination: Path, progress_callback: Optional[Callable[[int, int], None]] = None) -> Path:
    with requests.get(url, stream=True, timeout=30, headers=HTTP_HEADERS) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        downloaded = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)
    return destination


def _read_sha256_text(url: str) -> str:
    response = requests.get(url, timeout=15, headers=HTTP_HEADERS)
    response.raise_for_status()
    return response.text.strip()


def parse_sha256(text: str) -> Optional[str]:
    match = re.search(r"\b([a-fA-F0-9]{64})\b", text or "")
    return match.group(1).lower() if match else None


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_sha256(path: Path, expected: Optional[str]) -> bool:
    if not expected:
        return True
    return file_sha256(path) == expected.lower()


def download_and_prepare_update(update_info: Dict, progress_callback: Optional[Callable[[int, int], None]] = None) -> Path:
    installer_asset = update_info.get("installer_asset")
    if not installer_asset:
        raise RuntimeError("업데이트 설치 파일 자산을 찾을 수 없습니다.")
    installer_name = installer_asset.get("name") or "OSL_AI_Assistant_Setup.exe"
    temp_dir = Path(tempfile.gettempdir()) / "OSL AI Assistant Updates"
    installer_path = temp_dir / installer_name
    download_file(_asset_download_url(installer_asset), installer_path, progress_callback)

    sha_asset = update_info.get("sha_asset")
    expected_sha = None
    # If sha_asset is absent or no SHA can be parsed, skip SHA verification
    # and allow the update to proceed without a checksum check.
    if sha_asset:
        expected_sha = parse_sha256(_read_sha256_text(_asset_download_url(sha_asset)))
    if not verify_sha256(installer_path, expected_sha):
        raise RuntimeError("업데이트 파일 SHA256 검증에 실패했습니다.")
    return installer_path


def launch_installer(installer_path: Path) -> None:
    args = [str(installer_path), "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART"]
    if os.name == "nt":
        subprocess.Popen(args, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen(args)
