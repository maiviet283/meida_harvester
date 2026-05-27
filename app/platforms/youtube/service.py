from __future__ import annotations

from typing import Callable

from app.platforms.common import BaseDownloadService, PlatformConfig


YOUTUBE_FORMAT = "bv*+ba/b"


CONFIG = PlatformConfig(
    key="youtube",
    example_video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    example_page_url="https://www.youtube.com/@creator",
    supports_page_filters=True,
)


class YouTubeService(BaseDownloadService):
    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": YOUTUBE_FORMAT,
                "merge_output_format": "mp4",
            }
        )
        return options
