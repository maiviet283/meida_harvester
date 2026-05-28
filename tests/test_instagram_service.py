from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.platforms.common import BaseDownloadService, UserFacingDownloadError
from app.platforms.instagram.service import CONFIG, InstagramService


class InstagramServiceTest(unittest.TestCase):
    def test_full_page_download_has_no_short_long_filter(self) -> None:
        service = InstagramService()
        progress = lambda *args: None
        urls = ["https://www.instagram.com/reel/ABC123/"]

        with patch.object(service, "collect_profile_video_urls", return_value=urls) as collect, patch.object(
            service, "download_urls"
        ) as download_urls:
            service.download_page("https://www.instagram.com/creator/", "downloads", progress, page_filter="long")

        collect.assert_called_once_with("https://www.instagram.com/creator/", progress)
        download_urls.assert_called_once_with(
            urls,
            "downloads",
            progress,
            single=False,
            page_filter="all",
            emit_initial_progress=False,
        )
        self.assertFalse(CONFIG.supports_page_filters)
        self.assertTrue(CONFIG.supports_manual_cookies)

    def test_collects_profile_video_urls_with_feed_pagination(self) -> None:
        service = InstagramService()
        profile = {
            "data": {
                "user": {
                    "id": "123",
                    "is_private": False,
                    "edge_felix_video_timeline": {
                        "edges": [{"node": {"shortcode": "OLD1", "__typename": "GraphVideo"}}]
                    },
                    "edge_owner_to_timeline_media": {
                        "edges": [{"node": {"shortcode": "PHOTO1", "__typename": "GraphImage"}}]
                    },
                }
            }
        }
        first_page = {
            "items": [
                {"code": "old1", "media_type": 2, "product_type": "clips"},
                {"code": "NEW1", "media_type": 2, "product_type": "clips"},
                {"code": "NEW2", "media_type": 8, "carousel_media": [{"media_type": 1}, {"media_type": 2}]},
                {"code": "PHOTO2", "media_type": 1},
            ],
            "more_available": True,
            "next_max_id": "cursor-1",
        }
        second_page = {
            "items": [{"code": "NEW3", "video_versions": [{"url": "https://example.com/video.mp4"}]}],
            "more_available": False,
        }

        with patch.object(service, "fetch_json", side_effect=[profile, first_page, second_page]):
            urls = service.collect_profile_video_urls("https://www.instagram.com/creator/")

        self.assertEqual(
            urls,
            [
                "https://www.instagram.com/p/OLD1/",
                "https://www.instagram.com/reel/NEW1/",
                "https://www.instagram.com/p/NEW2/",
                "https://www.instagram.com/p/NEW3/",
            ],
        )

    def test_rejects_private_or_invalid_instagram_profile_pages(self) -> None:
        service = InstagramService()

        with self.assertRaises(UserFacingDownloadError) as context:
            service.collect_profile_video_urls("https://www.instagram.com/reel/ABC123/")
        self.assertEqual(context.exception.status_key, "instagram_page_link")

        with patch.object(
            service,
            "fetch_json",
            return_value={"data": {"user": {"id": "123", "is_private": True}}},
        ):
            with self.assertRaises(UserFacingDownloadError) as private_context:
                service.collect_profile_video_urls("https://www.instagram.com/private_creator/")
        self.assertEqual(private_context.exception.status_key, "instagram_private")

    def test_returns_profile_videos_when_feed_pagination_is_temporarily_blocked(self) -> None:
        service = InstagramService()
        profile = {
            "data": {
                "user": {
                    "id": "123",
                    "is_private": False,
                    "edge_felix_video_timeline": {
                        "edges": [{"node": {"shortcode": "OLD1", "__typename": "GraphVideo"}}]
                    },
                }
            }
        }

        with patch.object(service, "fetch_json", side_effect=[profile, UserFacingDownloadError("instagram_profile_failed")]):
            urls = service.collect_profile_video_urls("https://www.instagram.com/creator/")

        self.assertEqual(urls, ["https://www.instagram.com/p/OLD1/"])

    def test_options_prioritize_full_hd_mp4_and_skip_non_video_posts(self) -> None:
        service = InstagramService()

        with patch.object(service, "has_ffmpeg", return_value=True), patch.object(
            service, "find_browser_cookies", return_value=None
        ):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=False)

        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertIs(options["ignoreerrors"], True)
        self.assertIn("width<=1920][height<=1920", options["format"])
        self.assertIn("ba[ext=m4a]", options["format"])
        self.assertEqual(options["format_sort"][0], "quality")

        match_filter = options["match_filter"]
        self.assertIsNone(match_filter({"formats": [{"ext": "mp4", "vcodec": "avc1.640028", "acodec": "mp4a.40.2"}]}))
        self.assertEqual(
            match_filter({"formats": [{"ext": "jpg", "vcodec": "none", "acodec": "none"}]}),
            "Skipped Instagram non-video post",
        )
        self.assertIsNone(match_filter({"id": "ABC123"}, incomplete=True))
        with YoutubeDL(options):
            pass

    def test_options_use_browser_cookies_when_available(self) -> None:
        service = InstagramService()

        with patch.object(service, "has_ffmpeg", return_value=True), patch.object(
            service, "find_browser_cookies", return_value=("chrome", None, None, None)
        ):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

        self.assertEqual(options["cookiesfrombrowser"], ("chrome", None, None, None))
        with YoutubeDL(options):
            pass

    def test_options_prefer_manual_cookie_header_over_browser_cookies(self) -> None:
        service = InstagramService()
        service.set_manual_cookie_header("Cookie: sessionid=abc; ds_user_id=123")

        try:
            with patch.object(service, "has_ffmpeg", return_value=True), patch.object(
                service, "find_browser_cookies", return_value=("chrome", None, None, None)
            ):
                options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

            cookiefile_content = Path(options["cookiefile"]).read_text(encoding="utf-8")
            self.assertNotIn("cookiesfrombrowser", options)
            self.assertIn("sessionid\tabc", cookiefile_content)
            self.assertIn("ds_user_id\t123", cookiefile_content)
            with YoutubeDL(options):
                pass
        finally:
            service.cleanup_manual_cookie_files()

    def test_profile_api_uses_manual_cookie_header(self) -> None:
        service = InstagramService()
        service.set_manual_cookie_header("sessionid=abc")

        with patch.object(service, "open_json", return_value={}) as open_json:
            service.fetch_json("https://www.instagram.com/api/v1/test/")

        self.assertEqual(open_json.call_args.args[1]["Cookie"], "sessionid=abc")

    def test_options_do_not_require_ffmpeg_for_combined_instagram_video(self) -> None:
        service = InstagramService()

        with patch.object(service, "has_ffmpeg", return_value=False), patch.object(
            service, "find_browser_cookies", return_value=None
        ):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=True)

        self.assertIn("b[ext=mp4][width<=1920][height<=1920]", options["format"])
        self.assertNotIn("+ba", options["format"])
        self.assertNotIn("merge_output_format", options)
        with YoutubeDL(options):
            pass

    def test_accepts_and_normalizes_instagram_video_urls(self) -> None:
        service = InstagramService()

        for url in (
            "https://www.instagram.com/reel/ABC123/?igsh=test",
            "https://www.instagram.com/reels/ABC123/",
            "https://www.instagram.com/p/ABC123/",
            "https://www.instagram.com/tv/ABC123/",
        ):
            self.assertTrue(service.is_supported_video_url(url), url)

        self.assertEqual(
            service.normalize_video_url("https://www.instagram.com/reels/ABC123/?igsh=test"),
            "https://www.instagram.com/reel/ABC123/",
        )

    def test_rejects_non_video_single_links(self) -> None:
        service = InstagramService()

        with self.assertRaises(UserFacingDownloadError) as context:
            service.download_single("https://www.instagram.com/creator/", "downloads", lambda *args: None)

        self.assertEqual(context.exception.status_key, "instagram_single_link")

    def test_maps_instagram_image_post_to_friendly_error(self) -> None:
        service = InstagramService()

        error = service.to_user_error(DownloadError("ERROR: No video formats found"))

        self.assertEqual(error.status_key, "instagram_no_video")

    def test_maps_restricted_instagram_video_to_friendly_error(self) -> None:
        service = InstagramService()

        error = service.to_user_error(
            DownloadError(
                "ERROR: [Instagram] ABC123: This content isn't available to everyone: "
                "It can't be seen by certain audiences."
            )
        )

        self.assertEqual(error.status_key, "instagram_restricted")

    def test_maps_dpapi_cookie_error_to_retryable_instagram_message(self) -> None:
        service = InstagramService()

        error = service.to_user_error(DownloadError("ERROR: Failed to decrypt with DPAPI"))

        self.assertEqual(error.status_key, "instagram_cookie_failed")

    def test_retries_instagram_download_without_browser_cookies_after_dpapi_failure(self) -> None:
        service = InstagramService()
        calls: list[bool] = []

        def fake_download_urls(self, urls, folder, progress, single, page_filter, emit_initial_progress=True):
            calls.append(service.use_browser_cookies)
            if len(calls) == 1:
                raise UserFacingDownloadError("instagram_cookie_failed")

        with patch.object(BaseDownloadService, "download_urls", fake_download_urls):
            service.download_urls(
                ["https://www.instagram.com/reel/ABC123/"],
                "downloads",
                lambda *args: None,
                True,
                "all",
            )

        self.assertEqual(calls, [True, False])

    def test_reports_instagram_cookie_unavailable_when_public_retry_is_restricted(self) -> None:
        service = InstagramService()

        def fake_download_urls(self, urls, folder, progress, single, page_filter, emit_initial_progress=True):
            if service.use_browser_cookies:
                raise UserFacingDownloadError("instagram_cookie_failed")
            raise UserFacingDownloadError("instagram_restricted")

        with patch.object(BaseDownloadService, "download_urls", fake_download_urls):
            with self.assertRaises(UserFacingDownloadError) as context:
                service.download_urls(
                    ["https://www.instagram.com/reel/ABC123/"],
                    "downloads",
                    lambda *args: None,
                    True,
                    "all",
                )

        self.assertEqual(context.exception.status_key, "instagram_cookie_unavailable")

    def test_maps_broken_instagram_profile_extractor_to_friendly_error(self) -> None:
        service = InstagramService()

        error = service.to_user_error(DownloadError("ERROR: [instagram:user] creator: Unable to extract data"))

        self.assertEqual(error.status_key, "instagram_profile_failed")


if __name__ == "__main__":
    unittest.main()
