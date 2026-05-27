from __future__ import annotations

from typing import Callable

from app.platforms.common import BaseDownloadService, PlatformConfig


INSTAGRAM_FORMAT = "bv*+ba/b"


CONFIG = PlatformConfig(
    key="instagram",
    example_video_url="https://www.instagram.com/reel/ABC123/",
    example_page_url="https://www.instagram.com/creator/",
    supports_page_filters=True,
)


class InstagramService(BaseDownloadService):
    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": INSTAGRAM_FORMAT,
                "merge_output_format": "mp4",
            }
        )
        return options
