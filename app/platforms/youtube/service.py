from __future__ import annotations

from typing import Callable
from urllib.parse import parse_qs, urlparse

from yt_dlp.utils import DownloadError

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, UserFacingDownloadError


YOUTUBE_HIGH_QUALITY_FORMAT = "bv*+ba[ext=m4a]/bv*+ba/b"


CONFIG = PlatformConfig(
    key="youtube",
    example_video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    example_page_url="https://www.youtube.com/@creator",
    supports_page_filters=True,
)


class YouTubeService(BaseDownloadService):
    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        if not self.has_ffmpeg():
            raise UserFacingDownloadError("ffmpeg_missing")

        print(f"[YouTube] format: {YOUTUBE_HIGH_QUALITY_FORMAT}")
        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": YOUTUBE_HIGH_QUALITY_FORMAT,
                "merge_output_format": "mp4",
            }
        )
        if not single:
            options["ignoreerrors"] = "only_download"
        return options

    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        if not self.is_supported_video_url(url):
            raise UserFacingDownloadError("youtube_single_link")
        url = self.normalize_video_url(url)
        print(f"[YouTube] downloading single: {url}")
        super().download_single(url, folder, progress)

    def to_user_error(self, exc: DownloadError) -> UserFacingDownloadError:
        message = str(exc)
        lower_message = message.lower()
        print(f"[YouTube] download error: {message}")
        if "age" in lower_message and ("restricted" in lower_message or "confirm" in lower_message):
            return UserFacingDownloadError("youtube_age_restricted")
        if "members" in lower_message or "member-only" in lower_message or "join this channel" in lower_message:
            return UserFacingDownloadError("youtube_members_only")
        return super().to_user_error(exc)

    def normalize_video_url(self, url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path_parts = [p for p in parsed.path.split("/") if p]

        if host == "youtu.be" and path_parts:
            return f"https://www.youtube.com/watch?v={path_parts[0]}"

        if "youtube.com" in host and path_parts:
            if path_parts[0] == "shorts" and len(path_parts) >= 2:
                return f"https://www.youtube.com/watch?v={path_parts[1]}"
            if path_parts[0] == "live" and len(path_parts) >= 2:
                return f"https://www.youtube.com/watch?v={path_parts[1]}"

        return url

    def is_supported_video_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path = parsed.path.lower()
            query = parse_qs(parsed.query)
        except Exception:
            return False

        if host == "youtu.be":
            return bool(parsed.path.strip("/"))

        if "youtube.com" not in host:
            return False

        if path == "/watch" and "v" in query:
            return True
        if path.startswith("/shorts/") and len(path) > len("/shorts/"):
            return True
        if path.startswith("/live/") and len(path) > len("/live/"):
            return True
        if path.startswith("/embed/") and len(path) > len("/embed/"):
            return True
        if path.startswith("/v/") and len(path) > len("/v/"):
            return True

        return False
