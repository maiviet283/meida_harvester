from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request

from app.version import APP_VERSION, UPDATE_MANIFEST_URL


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    minimum_supported_version: str
    download_url: str
    release_url: str
    message: str


@dataclass(frozen=True)
class UpdateCheck:
    info: UpdateInfo
    available: bool
    required: bool


ProgressCallback = Callable[[int, int], None]


def is_packaged_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def fetch_update_manifest(url: str = UPDATE_MANIFEST_URL, timeout: int = 5) -> UpdateInfo:
    request = urllib.request.Request(url, headers={"User-Agent": "ClipFlow-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise UpdateError(str(exc)) from exc

    latest_version = str(payload.get("latest_version") or "").strip()
    if not latest_version:
        raise UpdateError("Update manifest is missing latest_version")

    return UpdateInfo(
        latest_version=latest_version,
        minimum_supported_version=str(
            payload.get("minimum_supported_version") or latest_version
        ).strip(),
        download_url=str(payload.get("download_url") or "").strip(),
        release_url=str(payload.get("release_url") or "").strip(),
        message=str(payload.get("message") or "").strip(),
    )


def check_for_update(current_version: str = APP_VERSION) -> UpdateCheck:
    info = fetch_update_manifest()
    return UpdateCheck(
        info=info,
        available=is_version_less(current_version, info.latest_version),
        required=is_version_less(current_version, info.minimum_supported_version),
    )


def is_version_less(current: str, target: str) -> bool:
    return parse_version(current) < parse_version(target)


def parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in re.split(r"[.+-]", version.strip().lower().lstrip("v")):
        match = re.match(r"\d+", token)
        parts.append(int(match.group(0)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def download_update(
    info: UpdateInfo,
    progress: ProgressCallback | None = None,
    timeout: int = 30,
) -> Path:
    if not info.download_url:
        raise UpdateError("Update manifest is missing download_url")

    update_dir = Path(tempfile.mkdtemp(prefix="clipflow_update_"))
    archive_path = update_dir / "ClipFlow-update.zip"
    request = urllib.request.Request(info.download_url, headers={"User-Agent": "ClipFlow-Updater"})

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with archive_path.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    file.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total)
    except (OSError, urllib.error.URLError) as exc:
        shutil.rmtree(update_dir, ignore_errors=True)
        raise UpdateError(str(exc)) from exc

    return archive_path


def start_self_update(archive_path: Path) -> None:
    if not is_packaged_app():
        raise UpdateError("Self-update is only available in packaged builds")

    app_pid = os.getpid()
    target_exe = Path(sys.executable).resolve()
    install_dir = target_exe.parent
    script_path = Path(tempfile.gettempdir()) / f"clipflow_update_{app_pid}.ps1"
    log_path = Path(tempfile.gettempdir()) / "clipflow_update.log"

    script_path.write_text(
        build_update_script(app_pid, archive_path, install_dir, target_exe, log_path),
        encoding="utf-8",
    )
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def build_update_script(
    app_pid: int,
    archive_path: Path,
    install_dir: Path,
    target_exe: Path,
    log_path: Path,
) -> str:
    return textwrap.dedent(
        f"""
        $ErrorActionPreference = "Stop"
        $pidToWait = {app_pid}
        $archivePath = {powershell_quote(str(archive_path))}
        $installDir = {powershell_quote(str(install_dir))}
        $targetExe = {powershell_quote(str(target_exe))}
        $logPath = {powershell_quote(str(log_path))}
        $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("clipflow_extract_" + [guid]::NewGuid().ToString())

        try {{
            Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 800
            New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
            Expand-Archive -LiteralPath $archivePath -DestinationPath $tempDir -Force

            $payload = $tempDir
            $children = @(Get-ChildItem -LiteralPath $tempDir -Force)
            if ($children.Count -eq 1 -and $children[0].PSIsContainer) {{
                $payload = $children[0].FullName
            }}

            Copy-Item -Path (Join-Path $payload '*') -Destination $installDir -Recurse -Force
            Start-Process -FilePath $targetExe -WorkingDirectory $installDir
        }} catch {{
            Add-Content -LiteralPath $logPath -Value $_.Exception.ToString()
        }} finally {{
            Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
        }}
        """
    ).strip()


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
