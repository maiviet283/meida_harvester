from __future__ import annotations

from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError


ProgressCallback = Callable[[str, int, dict[str, str] | None], None]


@dataclass(frozen=True)
class PlatformConfig:
    key: str
    example_video_url: str
    example_page_url: str
    supports_page_filters: bool = False
    supports_manual_cookies: bool = False
    supports_analysis: bool = False


class UserFacingDownloadError(Exception):
    def __init__(self, status_key: str, data: dict[str, str] | None = None) -> None:
        super().__init__(status_key)
        self.status_key = status_key
        self.data = data or {}


class YtDlpLogger:
    def _print(self, message: str) -> None:
        try:
            print(message)
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "utf-8"
            safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
            print(safe_message)

    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            return
        self._print(f"[yt-dlp] {message}")

    def warning(self, message: str) -> None:
        self._print(f"[yt-dlp] WARNING: {message}")

    def error(self, message: str) -> None:
        self._print(f"[yt-dlp] ERROR: {message}")


class BaseDownloadService:
    def request_cancel(self) -> None:
        self.cancel_requested = True

    def is_cancel_requested(self) -> bool:
        return bool(getattr(self, "cancel_requested", False))

    def raise_if_cancelled(self) -> None:
        if self.is_cancel_requested():
            raise DownloadCancelled()

    def clean_input_url(self, url: str) -> str:
        return url.strip()

    def set_manual_cookie_header(self, cookie_header: str) -> None:
        self.manual_cookie_header = self.normalize_cookie_header(cookie_header)

    def get_manual_cookie_header(self) -> str:
        return getattr(self, "manual_cookie_header", "")

    def normalize_cookie_header(self, cookie_header: str) -> str:
        lines = [line.strip() for line in cookie_header.replace("\r", "\n").split("\n") if line.strip()]
        for line in lines:
            if line.lower().startswith("cookie:"):
                return line.split(":", 1)[1].strip()
        return " ".join(lines)

    def apply_manual_cookies(self, options: dict, domains: tuple[str, ...]) -> bool:
        cookie_header = self.get_manual_cookie_header()
        if not cookie_header:
            return False
        cookies = self.parse_cookie_header(cookie_header)
        if not cookies:
            return False
        options["cookiefile"] = str(self.write_manual_cookie_file(cookies, domains))
        return True

    def parse_cookie_header(self, cookie_header: str) -> list[tuple[str, str]]:
        parsed = SimpleCookie()
        try:
            parsed.load(cookie_header)
        except CookieError:
            parsed = SimpleCookie()
        cookies = [(name, morsel.value) for name, morsel in parsed.items()]
        if cookies:
            return cookies

        fallback_cookies: list[tuple[str, str]] = []
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if name:
                fallback_cookies.append((name, value.strip()))
        return fallback_cookies

    def write_manual_cookie_file(self, cookies: list[tuple[str, str]], domains: tuple[str, ...]) -> Path:
        cookie_file = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            prefix="clipflow_cookies_",
            suffix=".txt",
        )
        path = Path(cookie_file.name)
        with cookie_file:
            cookie_file.write("# Netscape HTTP Cookie File\n")
            for domain in domains:
                normalized_domain = domain if domain.startswith(".") else f".{domain}"
                for name, value in cookies:
                    safe_name = name.replace("\t", "").replace("\r", "").replace("\n", "")
                    safe_value = value.replace("\t", "").replace("\r", "").replace("\n", "")
                    cookie_file.write(f"{normalized_domain}\tTRUE\t/\tTRUE\t2147483647\t{safe_name}\t{safe_value}\n")

        files = getattr(self, "_manual_cookie_files", [])
        files.append(path)
        self._manual_cookie_files = files
        return path

    def cleanup_manual_cookie_files(self) -> None:
        for path in getattr(self, "_manual_cookie_files", []):
            path.unlink(missing_ok=True)
        self._manual_cookie_files = []

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
        self.download_urls([url], folder, progress, single, page_filter)

    def download_urls(
        self,
        urls: list[str],
        folder: str,
        progress: ProgressCallback,
        single: bool,
        page_filter: str,
        emit_initial_progress: bool = True,
    ) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        if emit_initial_progress:
            progress("preparing", 8, None)

        def hook(info: dict) -> None:
            self.raise_if_cancelled()
            status = info.get("status")
            if status == "downloading":
                total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                downloaded = info.get("downloaded_bytes") or 0
                percent = int(downloaded * 100 / total) if total else 35
                progress("downloading", max(12, min(92, percent)), None)
            elif status == "finished":
                progress("processing", 95, None)

        try:
            options = self.build_yt_dlp_options(folder, hook, single)
            match_filter = self.build_cancelable_match_filter(
                options.get("match_filter"),
                page_filter,
                force=not single,
            )
            if match_filter:
                options["match_filter"] = match_filter

            if emit_initial_progress:
                self.raise_if_cancelled()
                progress("reading", 18, None)
            self.raise_if_cancelled()
            with YoutubeDL(options) as downloader:
                downloader.download(urls)
        except DownloadCancelled as exc:
            raise UserFacingDownloadError("download_cancelled") from exc
        except DownloadError as exc:
            raise self.to_user_error(exc) from exc
        finally:
            self.cleanup_manual_cookie_files()
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

    def build_cancelable_match_filter(
        self,
        platform_match_filter: Callable[[dict, bool], str | None] | None,
        page_filter: str,
        force: bool = False,
    ) -> Callable[[dict, bool], str | None] | None:
        duration_match_filter = self.build_match_filter(page_filter)
        filters = [match_filter for match_filter in (platform_match_filter, duration_match_filter) if match_filter]
        if not force and not filters and not self.is_cancel_requested():
            return None

        def match_filter(info: dict, *args, **kwargs) -> str | None:
            self.raise_if_cancelled()
            for active_filter in filters:
                reason = active_filter(info, *args, **kwargs)
                if reason:
                    return reason
            return None

        return match_filter

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
