from __future__ import annotations

import unittest
from unittest.mock import patch

from yt_dlp import YoutubeDL

from app.platforms.common import UserFacingDownloadError
from app.platforms.youtube.service import CONFIG, YouTubeService


class YouTubeServiceTest(unittest.TestCase):
    def test_requires_ffmpeg_for_high_quality_downloads(self) -> None:
        service = YouTubeService()

        with patch.object(service, "has_ffmpeg", return_value=False):
            with self.assertRaises(UserFacingDownloadError) as context:
                service.build_yt_dlp_options("downloads", lambda info: None, single=True)

        self.assertEqual(context.exception.status_key, "ffmpeg_missing")

    def test_uses_default_clients_and_split_formats_when_ffmpeg_available(self) -> None:
        service = YouTubeService()

        with patch.object(service, "has_ffmpeg", return_value=True):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

        self.assertEqual(options["format"], "bv*+ba[ext=m4a]/bv*+ba/b")
        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertNotIn("extractor_args", options)
        with YoutubeDL(options):
            pass

    def test_page_download_has_no_short_long_filter(self) -> None:
        service = YouTubeService()
        progress = lambda *args: None

        with patch.object(service, "download") as download:
            service.download_page("https://www.youtube.com/@creator", "downloads", progress, page_filter="short")

        download.assert_called_once_with(
            "https://www.youtube.com/@creator",
            "downloads",
            progress,
            single=False,
            page_filter="all",
        )
        self.assertFalse(CONFIG.supports_page_filters)

    def test_normalizes_youtube_video_url_shapes(self) -> None:
        service = YouTubeService()

        self.assertEqual(
            service.normalize_video_url("https://youtu.be/dQw4w9WgXcQ"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        self.assertEqual(
            service.normalize_video_url("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        self.assertEqual(
            service.normalize_video_url("https://www.youtube.com/live/dQw4w9WgXcQ"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )


if __name__ == "__main__":
    unittest.main()
