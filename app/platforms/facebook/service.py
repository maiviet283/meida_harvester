from __future__ import annotations

from typing import Callable

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, UserFacingDownloadError


FACEBOOK_FORMAT = "bv*+ba/b"


CONFIG = PlatformConfig(
    key="facebook",
    example_video_url="https://www.facebook.com/watch/?v=123456789",
    example_page_url="https://www.facebook.com/example.page",
    supports_page_filters=True,
)


class FacebookService(BaseDownloadService):
    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": FACEBOOK_FORMAT,
                "merge_output_format": "mp4",
            }
        )
        return options

    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        if not self.is_supported_video_url(url):
            raise UserFacingDownloadError("facebook_single_link")
        super().download_single(url, folder, progress)

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "short",
    ) -> None:
        if "/people/" in url:
            raise UserFacingDownloadError("facebook_people_page")
        super().download_page(url, folder, progress, page_filter)

    def is_supported_video_url(self, url: str) -> bool:
        normalized = url.lower()
        return any(
            marker in normalized
            for marker in (
                "/watch",
                "/videos/",
                "/reel/",
                "/share/v/",
                "/share/r/",
                "fb.watch/",
            )
        )
