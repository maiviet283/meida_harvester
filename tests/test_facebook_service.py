from __future__ import annotations

from pathlib import Path
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

        self.assertFalse(service.is_supported_video_url("https://www.facebook.com/HaiBuaNhan/"))
        self.assertFalse(service.is_supported_video_url("https://www.facebook.com/HaiBuaNhan/videos/"))

    def test_full_page_download_collects_plain_profile_links_before_downloading(self) -> None:
        service = FacebookService()
        progress = lambda *args: None
        urls = ["https://www.facebook.com/watch/?v=123456789"]

        with patch.object(service, "collect_page_video_urls", return_value=urls) as collect, patch.object(
            service, "download_urls"
        ) as download_urls:
            service.download_page("https://www.facebook.com/HaiBuaNhan/", "downloads", progress, page_filter="short")

        collect.assert_called_once_with("https://www.facebook.com/HaiBuaNhan/", progress)
        download_urls.assert_called_once_with(
            urls,
            "downloads",
            progress,
            single=False,
            page_filter="short",
            emit_initial_progress=False,
        )

    def test_collects_facebook_video_urls_from_page_html(self) -> None:
        service = FacebookService()
        html = r"""
            <a href="/watch/?v=111111111111111">watch</a>
            <a href="https://www.facebook.com/reel/222222222222222/">reel</a>
            {"video_id":"333333333333333"}
            https:\/\/www.facebook.com\/HaiBuaNhan\/videos\/vb.1\/444444444444444\/
            <a href="/HaiBuaNhan/videos?cursor=next">More</a>
        """
        next_html = """<a href="/watch/?v=555555555555555">next</a>"""

        with patch.object(
            service,
            "build_page_scan_urls",
            return_value=["https://www.facebook.com/HaiBuaNhan/videos"],
        ), patch.object(service, "fetch_page_html", side_effect=[html, next_html]):
            urls = service.collect_page_video_urls("https://www.facebook.com/HaiBuaNhan/")

        self.assertEqual(
            urls,
            [
                "https://www.facebook.com/watch/?v=111111111111111",
                "https://www.facebook.com/reel/222222222222222/",
                "https://www.facebook.com/watch/?v=444444444444444",
                "https://www.facebook.com/watch/?v=333333333333333",
                "https://www.facebook.com/watch/?v=555555555555555",
            ],
        )

    def test_rejects_invalid_facebook_page_links(self) -> None:
        service = FacebookService()

        with self.assertRaises(UserFacingDownloadError) as context:
            service.collect_page_video_urls("https://www.facebook.com/watch/?v=123")

        self.assertEqual(context.exception.status_key, "facebook_page_link")

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

    def test_options_prefer_manual_cookie_header_over_browser_cookies(self) -> None:
        service = FacebookService()
        service.set_manual_cookie_header("Cookie: c_user=1; xs=2")

        try:
            with patch.object(service, "has_ffmpeg", return_value=True), patch.object(
                service, "find_browser_cookies", return_value=("edge", None, None, None)
            ):
                options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

            cookiefile_content = Path(options["cookiefile"]).read_text(encoding="utf-8")
            self.assertNotIn("cookiesfrombrowser", options)
            self.assertIn("c_user\t1", cookiefile_content)
            self.assertIn("xs\t2", cookiefile_content)
            with YoutubeDL(options):
                pass
        finally:
            service.cleanup_manual_cookie_files()

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
