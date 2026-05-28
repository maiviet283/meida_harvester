from __future__ import annotations

import os
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from yt_dlp.utils import DownloadError

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, UserFacingDownloadError


FACEBOOK_COMBINED_FORMAT = "b[ext=mp4]/b"
FACEBOOK_SPLIT_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b"
FACEBOOK_BROWSER_COOKIE_PATHS = (
    ("firefox", ("APPDATA", "Mozilla", "Firefox", "Profiles")),
    ("edge", ("LOCALAPPDATA", "Microsoft", "Edge", "User Data")),
    ("chrome", ("LOCALAPPDATA", "Google", "Chrome", "User Data")),
)


CONFIG = PlatformConfig(
    key="facebook",
    example_video_url="https://www.facebook.com/watch/?v=123456789",
    example_page_url="https://www.facebook.com/example.page",
    supports_page_filters=True,
)


class FacebookService(BaseDownloadService):
    def download(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        single: bool,
        page_filter: str,
    ) -> None:
        self.use_browser_cookies = True
        self.browser_cookie_failed = False
        try:
            super().download(url, folder, progress, single, page_filter)
        except UserFacingDownloadError as exc:
            if exc.status_key != "facebook_cookie_failed":
                raise
            self.browser_cookie_failed = True
            self.use_browser_cookies = False
            try:
                super().download(url, folder, progress, single, page_filter)
            except UserFacingDownloadError as retry_exc:
                if retry_exc.status_key in {"facebook_parse_failed", "login_required"}:
                    raise UserFacingDownloadError("facebook_cookie_unavailable") from retry_exc
                raise
        finally:
            self.use_browser_cookies = True
            self.browser_cookie_failed = False

    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        has_ffmpeg = self.has_ffmpeg()
        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": FACEBOOK_SPLIT_FORMAT if has_ffmpeg else FACEBOOK_COMBINED_FORMAT,
                "http_headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            }
        )
        if has_ffmpeg:
            options["merge_output_format"] = "mp4"
        if getattr(self, "use_browser_cookies", True):
            cookies_from_browser = self.find_browser_cookies()
            if cookies_from_browser:
                options["cookiesfrombrowser"] = cookies_from_browser
        return options

    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        if not self.is_supported_video_url(url):
            raise UserFacingDownloadError("facebook_single_link")
        url = self.normalize_video_url(url)
        super().download_single(url, folder, progress)

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "short",
    ) -> None:
        normalized = url.lower()
        if "/people/" in normalized:
            raise UserFacingDownloadError("facebook_people_page")
        super().download_page(url, folder, progress, page_filter)

    def to_user_error(self, exc: DownloadError) -> UserFacingDownloadError:
        message = str(exc)
        lower_message = message.lower()
        if "failed to decrypt with dpapi" in lower_message:
            return UserFacingDownloadError("facebook_cookie_failed")
        if "cannot parse data" in lower_message:
            if getattr(self, "browser_cookie_failed", False):
                return UserFacingDownloadError("facebook_cookie_unavailable")
            return UserFacingDownloadError("facebook_parse_failed")
        return super().to_user_error(exc)

    def normalize_video_url(self, url: str) -> str:
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 3 and path_parts[0] == "share":
            share_type = path_parts[1]
            shared_id = path_parts[2]
            if shared_id.isdigit() and share_type == "r":
                return f"https://www.facebook.com/reel/{shared_id}/"
            if shared_id.isdigit() and share_type == "v":
                return f"https://www.facebook.com/watch/?v={shared_id}"
        return url

    def is_supported_video_url(self, url: str) -> bool:
        normalized = url.lower()
        return any(
            marker in normalized
            for marker in (
                "/watch",
                "/video.php",
                "/video/video.php",
                "/videos/",
                "/reel/",
                "/share/v/",
                "/share/r/",
                "fb.watch/",
                "/posts/",
                "/permalink.php",
                "/story.php",
                "/groups/",
                "/events/",
            )
        )

    def find_browser_cookies(self) -> tuple[str, str | None, str | None, str | None] | None:
        for browser, path_parts in FACEBOOK_BROWSER_COOKIE_PATHS:
            env_name, *relative_parts = path_parts
            root = os.environ.get(env_name)
            if root and (Path(root).joinpath(*relative_parts)).exists():
                return (browser, None, None, None)
        return None
