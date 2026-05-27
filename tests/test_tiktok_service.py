from __future__ import annotations

import unittest
from unittest.mock import patch

from yt_dlp import YoutubeDL

from app.platforms.tiktok.service import TikTokService


class TikTokServiceTest(unittest.TestCase):
    def test_single_download_keeps_strict_error_behavior(self) -> None:
        service = TikTokService()

        options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

        self.assertTrue(options["noplaylist"])
        self.assertNotIn("ignoreerrors", options)
        self.assertNotIn("match_filter", options)
        self.assertIn("b[ext=mp4][vcodec=h264][acodec=aac]", options["format"])
        self.assertIn("b[vcodec!=none][acodec!=none]", options["format"])
        with YoutubeDL(options):
            pass

    def test_page_download_skips_non_video_posts_without_aborting_batch(self) -> None:
        service = TikTokService()

        options = service.build_yt_dlp_options("downloads", lambda info: None, single=False)
        match_filter = options["match_filter"]

        self.assertFalse(options["noplaylist"])
        self.assertEqual(options["ignoreerrors"], "only_download")
        self.assertIsNone(match_filter({"formats": [{"ext": "mp4", "vcodec": "h264", "acodec": "aac"}]}))
        self.assertEqual(
            match_filter({"formats": [{"ext": "m4a", "vcodec": "none", "acodec": "aac"}]}),
            "Skipped TikTok image/slideshow post",
        )
        self.assertIsNone(match_filter({"id": "123"}, incomplete=True))
        with YoutubeDL(options):
            pass

    def test_ffmpeg_enables_split_video_audio_formats(self) -> None:
        service = TikTokService()

        with patch.object(service, "has_ffmpeg", return_value=True):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=False)

        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertIn("bv*[ext=mp4][vcodec=h264]+ba[ext=m4a]", options["format"])
        self.assertIn("bv*[vcodec!=none]+ba", options["format"])
        with YoutubeDL(options):
            pass


if __name__ == "__main__":
    unittest.main()
