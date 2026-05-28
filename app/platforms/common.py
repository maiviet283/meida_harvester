from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


ProgressCallback = Callable[[str, int, dict[str, str] | None], None]


@dataclass(frozen=True)
class PlatformConfig:
    key: str
    example_video_url: str
    example_page_url: str
    supports_page_filters: bool = False


class UserFacingDownloadError(Exception):
    def __init__(self, status_key: str, data: dict[str, str] | None = None) -> None:
        super().__init__(status_key)
        self.status_key = status_key
        self.data = data or {}


class YtDlpLogger:
    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            return
        print(f"[yt-dlp] {message}")

    def warning(self, message: str) -> None:
        print(f"[yt-dlp] WARNING: {message}")

    def error(self, message: str) -> None:
        print(f"[yt-dlp] ERROR: {message}")


class BaseDownloadService:
    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        self.download(url, folder, progress, single=True, page_filter="all")

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "all",
    ) -> None:
        self.download(url, folder, progress, single=False, page_filter=page_filter)

    def download(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        single: bool,
        page_filter: str,
    ) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        progress("preparing", 8, None)

        def hook(info: dict) -> None:
            status = info.get("status")
            if status == "downloading":
                total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                downloaded = info.get("downloaded_bytes") or 0
                percent = int(downloaded * 100 / total) if total else 35
                progress("downloading", max(12, min(92, percent)), None)
            elif status == "finished":
                progress("processing", 95, None)

        options = self.build_yt_dlp_options(folder, hook, single)
        match_filter = self.build_match_filter(page_filter)
        if match_filter:
            options["match_filter"] = match_filter

        progress("reading", 18, None)
        try:
            with YoutubeDL(options) as downloader:
                downloader.download([url])
        except DownloadError as exc:
            raise self.to_user_error(exc) from exc
        progress("finished", 100, None)

    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        raise NotImplementedError("Platform service must define yt-dlp options")

    def base_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        options = {
            "outtmpl": str(Path(folder) / "%(extractor)s" / "%(uploader|Creator)s" / "%(title).90s [%(id)s].%(ext)s"),
            "noplaylist": single,
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "logger": YtDlpLogger(),
        }
        ffmpeg_location = self.find_bundled_ffmpeg_location()
        if ffmpeg_location:
            options["ffmpeg_location"] = ffmpeg_location
        return options

    def has_ffmpeg(self) -> bool:
        return self.find_bundled_ffmpeg_location() is not None or shutil.which("ffmpeg") is not None

    def find_bundled_ffmpeg_location(self) -> str | None:
        ffmpeg_root = Path(__file__).resolve().parents[2] / "ffmpeg"
        for directory in (ffmpeg_root / "bin", ffmpeg_root):
            if (directory / "ffmpeg.exe").is_file() or (directory / "ffmpeg").is_file():
                return str(directory)
        return None

    def to_user_error(self, exc: DownloadError) -> UserFacingDownloadError:
        message = str(exc)
        lower_message = message.lower()
        if "unsupported url" in lower_message:
            return UserFacingDownloadError("unsupported_url")
        if "requested format is not available" in lower_message:
            return UserFacingDownloadError("unsupported_codec")
        if "ffmpeg" in lower_message and ("not found" in lower_message or "not installed" in lower_message):
            return UserFacingDownloadError("ffmpeg_missing")
        if "cannot parse data" in lower_message:
            return UserFacingDownloadError("extractor_changed")
        if "login" in lower_message or "sign in" in lower_message:
            return UserFacingDownloadError("login_required")
        return UserFacingDownloadError("download_failed", {"error": message})

    def build_match_filter(self, page_filter: str) -> Callable[[dict, bool], str | None] | None:
        if page_filter == "short":
            return lambda info, *args, **kwargs: self.reject_by_duration(info, max_seconds=180)
        if page_filter == "long":
            return lambda info, *args, **kwargs: self.reject_by_duration(info, min_seconds=181)
        return None

    def reject_by_duration(
        self,
        info: dict,
        min_seconds: int | None = None,
        max_seconds: int | None = None,
    ) -> str | None:
        duration = info.get("duration")
        if duration is None:
            return None
        if min_seconds is not None and duration < min_seconds:
            return "Skipped short video"
        if max_seconds is not None and duration > max_seconds:
            return "Skipped long video"
        return None
