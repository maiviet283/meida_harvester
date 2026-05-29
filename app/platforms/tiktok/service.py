from __future__ import annotations

import re
from typing import Callable

from yt_dlp import YoutubeDL

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, YtDlpLogger


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
TIKTOK_FALLBACK_VIDEO_FORMATS = (
    "b[ext=mp4][vcodec!=none][acodec!=none]",
    "b[vcodec!=none][acodec!=none]",
)
TIKTOK_FALLBACK_SPLIT_FORMATS = (
    "bv*[ext=mp4][vcodec!=none]+ba[ext=m4a]",
    "bv*[vcodec!=none]+ba",
)


CONFIG = PlatformConfig(
    key="tiktok",
    example_video_url="https://www.tiktok.com/@creator/video/123456789",
    example_page_url="https://www.tiktok.com/@creator",
    supports_analysis=True,
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
        formats.extend(TIKTOK_FALLBACK_VIDEO_FORMATS)
        if has_ffmpeg:
            formats.extend(TIKTOK_FALLBACK_SPLIT_FORMATS)

        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": "/".join(formats),
                "format_sort": ["vcodec:h264", "quality", "res", "fps", "acodec:aac"],
            }
        )
        if has_ffmpeg:
            options["merge_output_format"] = "mp4"
        if not single:
            options["ignoreerrors"] = "only_download"
            options["match_filter"] = self.reject_non_video_post
        return options

    def reject_non_video_post(self, info: dict, *args, **kwargs) -> str | None:
        if kwargs.get("incomplete"):
            return None

        formats = info.get("formats") or []
        if not formats:
            return None if self.has_video_track(info) else "Skipped TikTok image/slideshow post"
        if any(self.has_video_track(format_info) for format_info in formats):
            return None
        return "Skipped TikTok image/slideshow post"

    def has_video_track(self, format_info: dict) -> bool:
        vcodec = format_info.get("vcodec")
        if vcodec and vcodec != "none":
            return True
        return vcodec is None and format_info.get("ext") in {"mp4", "mov", "webm", "mkv"}

    def analyze_page(
        self,
        url: str,
        progress: ProgressCallback,
        on_video: "Callable[[dict], None] | None" = None,
        on_channel: "Callable[[dict], None] | None" = None,
    ) -> list[dict]:
        # Phase 1 — fast flat listing to discover all video URLs + channel info
        progress("analyze_reading", 5, None)
        flat_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "logger": YtDlpLogger(),
            "ignoreerrors": True,
            "extract_flat": "in_playlist",
        }
        with YoutubeDL(flat_opts) as ydl:
            flat_info = ydl.extract_info(url, download=False)

        self.raise_if_cancelled()

        # Emit channel metadata immediately after flat extraction
        if flat_info and on_channel:
            channel_info = {
                "name": flat_info.get("uploader") or flat_info.get("channel") or "",
                "username": flat_info.get("uploader_id") or flat_info.get("channel_id") or "",
                "bio": flat_info.get("description") or "",
            }
            on_channel(channel_info)

        raw: list[dict] = []
        if flat_info:
            if flat_info.get("entries") is not None:
                raw = [e for e in (flat_info.get("entries") or []) if e and isinstance(e, dict)]
            else:
                raw = [flat_info]

        total = len(raw)
        if total == 0:
            progress("analyze_done", 100, {"count": "0"})
            return []

        progress("analyze_video", 10, {"current": "0", "total": str(total)})

        # Phase 2 — full extract per video to get hashtags + accurate stats
        full_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "logger": YtDlpLogger(),
            "ignoreerrors": True,
            "noplaylist": True,
        }
        ffmpeg = self.find_bundled_ffmpeg_location()
        if ffmpeg:
            full_opts["ffmpeg_location"] = ffmpeg

        videos: list[dict] = []
        ydl = YoutubeDL(full_opts)
        for i, flat_entry in enumerate(raw):
            self.raise_if_cancelled()
            video_url = flat_entry.get("webpage_url") or flat_entry.get("url", "")
            if not video_url or not video_url.startswith("http"):
                continue
            try:
                entry = ydl.extract_info(video_url, download=False)
                if entry:
                    video = self._make_video_summary(entry)
                    if video:
                        videos.append(video)
                        if on_video:
                            on_video(video)
            except Exception:
                pass
            percent = 10 + int((i + 1) / total * 85)
            progress("analyze_video", percent, {"current": str(i + 1), "total": str(total)})

        progress("analyze_done", 100, {"count": str(len(videos))})
        return videos

    def _make_video_summary(self, entry: dict) -> dict | None:
        url = entry.get("webpage_url") or entry.get("url") or entry.get("original_url", "")
        if not url or not url.startswith("http"):
            return None

        raw_tags = entry.get("tags") or []
        if not raw_tags:
            desc = str(entry.get("description") or entry.get("title") or "")
            raw_tags = re.findall(r"#(\w+)", desc)
        hashtags = " ".join(f"#{t}" for t in raw_tags if isinstance(t, str)) if raw_tags else ""

        view_count = int(entry.get("view_count") or 0)
        like_count = int(entry.get("like_count") or 0)
        comment_count = int(entry.get("comment_count") or 0)
        repost_count = int(entry.get("repost_count") or 0)
        engage_rate = round((like_count + comment_count + repost_count) / max(view_count, 1) * 100, 1)

        return {
            "url": url,
            "title": entry.get("title") or entry.get("fulltitle") or entry.get("description", ""),
            "view_count": view_count,
            "like_count": like_count,
            "comment_count": comment_count,
            "repost_count": repost_count,
            "engage_rate": engage_rate,
            "duration": int(entry.get("duration") or 0),
            "upload_date": str(entry.get("upload_date") or ""),
            "hashtags": hashtags,
        }
