from __future__ import annotations

from typing import Callable

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback


TIKTOK_COMBINED_MP4_FORMATS = (
    "b[ext=mp4][vcodec=h264][acodec=aac]",
    "b[ext=mp4][vcodec^=avc1][acodec^=mp4a]",
    "b[ext=mp4][vcodec=h264][acodec!=none]",
    "b[ext=mp4][vcodec^=avc1][acodec!=none]",
)
TIKTOK_SPLIT_MP4_FORMATS = (
    "bv*[ext=mp4][vcodec=h264]+ba[ext=m4a]",
    "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]",
)


CONFIG = PlatformConfig(
    key="tiktok",
    example_video_url="https://www.tiktok.com/@creator/video/123456789",
    example_page_url="https://www.tiktok.com/@creator",
)


class TikTokService(BaseDownloadService):
    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        self.download(url, folder, progress, single=True, page_filter="all")

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "all",
    ) -> None:
        self.download(url, folder, progress, single=False, page_filter="all")

    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        formats = list(TIKTOK_COMBINED_MP4_FORMATS)
        has_ffmpeg = self.has_ffmpeg()
        if has_ffmpeg:
            formats.extend(TIKTOK_SPLIT_MP4_FORMATS)

        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": "/".join(formats),
                "format_sort": ["vcodec:h264", "quality", "res", "fps", "acodec:aac"],
            }
        )
        if has_ffmpeg:
            options["merge_output_format"] = "mp4"
        return options
