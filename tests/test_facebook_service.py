from __future__ import annotations

import unittest
from unittest.mock import patch

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.platforms.common import BaseDownloadService, UserFacingDownloadError
from app.platforms.facebook.service import FacebookService


class FacebookServiceTest(unittest.TestCase):
    def test_normalizes_supported_share_links(self) -> None:
        service = FacebookService()

        self.assertEqual(
            service.normalize_video_url("https://www.facebook.com/share/r/123456789/"),
            "https://www.facebook.com/reel/123456789/",
        )
        self.assertEqual(
            service.normalize_video_url("https://www.facebook.com/share/v/123456789/"),
            "https://www.facebook.com/watch/?v=123456789",
        )

    def test_accepts_common_facebook_video_url_shapes(self) -> None:
        service = FacebookService()

        for url in (
            "https://www.facebook.com/watch/?v=123",
            "https://www.facebook.com/user/videos/123/",
            "https://www.facebook.com/reel/123/",
            "https://www.facebook.com/share/v/123/",
            "https://www.facebook.com/story.php?story_fbid=123&id=456",
            "https://www.facebook.com/groups/example/posts/123/",
        ):
            self.assertTrue(service.is_supported_video_url(url), url)

    def test_options_do_not_require_ffmpeg_for_combined_video(self) -> None:
        service = FacebookService()

        with patch.object(service, "has_ffmpeg", return_value=False), patch.object(
            service, "find_browser_cookies", return_value=None
        ):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

        self.assertEqual(options["format"], "b[ext=mp4]/b")
        self.assertNotIn("merge_output_format", options)
        self.assertNotIn("cookiesfrombrowser", options)
        with YoutubeDL(options):
            pass

    def test_options_use_browser_cookies_and_split_formats_when_available(self) -> None:
        service = FacebookService()

        with patch.object(service, "has_ffmpeg", return_value=True), patch.object(
            service, "find_browser_cookies", return_value=("edge", None, None, None)
        ):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertEqual(options["cookiesfrombrowser"], ("edge", None, None, None))
        self.assertIn("bv*[ext=mp4]+ba[ext=m4a]", options["format"])
        with YoutubeDL(options):
            pass

    def test_maps_facebook_parse_error_to_specific_message(self) -> None:
        service = FacebookService()

        error = service.to_user_error(DownloadError("ERROR: Cannot parse data"))

        self.assertIsInstance(error, UserFacingDownloadError)
        self.assertEqual(error.status_key, "facebook_parse_failed")

    def test_maps_dpapi_cookie_error_to_retryable_message(self) -> None:
        service = FacebookService()

        error = service.to_user_error(DownloadError("ERROR: Failed to decrypt with DPAPI"))

        self.assertEqual(error.status_key, "facebook_cookie_failed")

    def test_retries_without_browser_cookies_after_dpapi_failure(self) -> None:
        service = FacebookService()
        calls: list[bool] = []

        def fake_download(self, url, folder, progress, single, page_filter):
            calls.append(service.use_browser_cookies)
            if len(calls) == 1:
                raise UserFacingDownloadError("facebook_cookie_failed")

        with patch.object(BaseDownloadService, "download", fake_download):
            service.download("https://www.facebook.com/watch/?v=123", "downloads", lambda *args: None, True, "all")

        self.assertEqual(calls, [True, False])

    def test_reports_cookie_unavailable_when_public_retry_still_needs_cookies(self) -> None:
        service = FacebookService()

        def fake_download(self, url, folder, progress, single, page_filter):
            if service.use_browser_cookies:
                raise UserFacingDownloadError("facebook_cookie_failed")
            raise UserFacingDownloadError("facebook_parse_failed")

        with patch.object(BaseDownloadService, "download", fake_download):
            with self.assertRaises(UserFacingDownloadError) as context:
                service.download("https://www.facebook.com/watch/?v=123", "downloads", lambda *args: None, True, "all")

        self.assertEqual(context.exception.status_key, "facebook_cookie_unavailable")


if __name__ == "__main__":
    unittest.main()
