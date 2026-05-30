from __future__ import annotations

import tempfile
import unittest
from typing import Callable

from yt_dlp.utils import DownloadCancelled

from app.platforms.common import BaseDownloadService, UserFacingDownloadError


class DummyDownloadService(BaseDownloadService):
    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        return self.base_yt_dlp_options(folder, hook, single)


class BaseDownloadServiceTest(unittest.TestCase):
    def test_cancelled_download_stops_before_ytdlp_runs(self) -> None:
        service = DummyDownloadService()
        service.request_cancel()

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(UserFacingDownloadError) as context:
                service.download_urls(
                    ["https://example.com/video"],
                    folder,
                    lambda *args: None,
                    single=False,
                    page_filter="all",
                )

        self.assertEqual(context.exception.status_key, "download_cancelled")

    def test_cancelable_match_filter_preserves_platform_and_duration_filters(self) -> None:
        service = DummyDownloadService()

        def platform_filter(info: dict, *args, **kwargs) -> str | None:
            return "Platform skip" if info.get("skip") else None

        match_filter = service.build_cancelable_match_filter(platform_filter, "short", force=True)

        self.assertIsNotNone(match_filter)
        self.assertEqual(match_filter({"skip": True}), "Platform skip")
        self.assertEqual(match_filter({"duration": 220}), "Skipped long video")
        self.assertIsNone(match_filter({"duration": 120}))

        service.request_cancel()
        with self.assertRaises(DownloadCancelled):
            match_filter({"duration": 120})


class NormalizeCookieHeaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyDownloadService()

    def test_strips_cookie_prefix_from_header_string(self) -> None:
        self.assertEqual(
            self.service.normalize_cookie_header("Cookie: sessionid=abc; ds_user_id=123"),
            "sessionid=abc; ds_user_id=123",
        )

    def test_strips_cookie_prefix_case_insensitively(self) -> None:
        self.assertEqual(
            self.service.normalize_cookie_header("COOKIE: token=xyz"),
            "token=xyz",
        )

    def test_joins_multiline_input_into_single_line(self) -> None:
        result = self.service.normalize_cookie_header("sessionid=abc\nds_user_id=123")
        self.assertIn("sessionid=abc", result)
        self.assertIn("ds_user_id=123", result)

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(self.service.normalize_cookie_header("  sessionid=abc  "), "sessionid=abc")


class ParseCookieHeaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyDownloadService()

    def test_parses_standard_cookie_string(self) -> None:
        cookies = self.service.parse_cookie_header("sessionid=abc; ds_user_id=123")
        self.assertIn(("sessionid", "abc"), cookies)
        self.assertIn(("ds_user_id", "123"), cookies)

    def test_falls_back_to_split_for_complex_values(self) -> None:
        cookies = self.service.parse_cookie_header("token=xyz; other=val")
        keys = [name for name, _ in cookies]
        self.assertIn("token", keys)
        self.assertIn("other", keys)

    def test_returns_empty_list_for_blank_input(self) -> None:
        self.assertEqual(self.service.parse_cookie_header(""), [])


class WriteCookieFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyDownloadService()

    def tearDown(self) -> None:
        self.service.cleanup_manual_cookie_files()

    def test_writes_valid_netscape_cookie_file(self) -> None:
        cookies = [("sessionid", "abc123"), ("ds_user_id", "999")]
        path = self.service.write_manual_cookie_file(cookies, (".instagram.com",))
        content = path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("# Netscape HTTP Cookie File"))
        self.assertIn(".instagram.com", content)
        self.assertIn("sessionid\tabc123", content)
        self.assertIn("ds_user_id\t999", content)

    def test_writes_cookies_for_all_specified_domains(self) -> None:
        cookies = [("c_user", "1")]
        path = self.service.write_manual_cookie_file(cookies, (".facebook.com", ".fb.watch"))
        content = path.read_text(encoding="utf-8")
        self.assertIn(".facebook.com", content)
        self.assertIn(".fb.watch", content)

    def test_normalizes_domain_without_leading_dot(self) -> None:
        cookies = [("token", "abc")]
        path = self.service.write_manual_cookie_file(cookies, ("instagram.com",))
        content = path.read_text(encoding="utf-8")
        self.assertIn(".instagram.com", content)

    def test_cleanup_deletes_written_files(self) -> None:
        cookies = [("sessionid", "abc")]
        path = self.service.write_manual_cookie_file(cookies, (".instagram.com",))
        self.assertTrue(path.exists())
        self.service.cleanup_manual_cookie_files()
        self.assertFalse(path.exists())

    def test_apply_manual_cookies_returns_false_when_no_cookie_set(self) -> None:
        options: dict = {}
        self.assertFalse(self.service.apply_manual_cookies(options, (".instagram.com",)))
        self.assertNotIn("cookiefile", options)

    def test_apply_manual_cookies_writes_file_and_sets_option(self) -> None:
        self.service.set_manual_cookie_header("sessionid=abc")
        options: dict = {}
        try:
            result = self.service.apply_manual_cookies(options, (".instagram.com",))
            self.assertTrue(result)
            self.assertIn("cookiefile", options)
        finally:
            self.service.cleanup_manual_cookie_files()


class ToUserErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyDownloadService()

    def _err(self, message: str):
        from yt_dlp.utils import DownloadError
        return self.service.to_user_error(DownloadError(message))

    def test_maps_unsupported_url_message(self) -> None:
        self.assertEqual(self._err("Unsupported URL").status_key, "unsupported_url")

    def test_maps_ffmpeg_not_found_message(self) -> None:
        self.assertEqual(self._err("ffmpeg not found").status_key, "ffmpeg_missing")

    def test_maps_ffmpeg_not_installed_message(self) -> None:
        self.assertEqual(self._err("ffmpeg not installed").status_key, "ffmpeg_missing")

    def test_maps_login_required_message(self) -> None:
        self.assertEqual(self._err("Please login to watch this video").status_key, "login_required")

    def test_maps_sign_in_message(self) -> None:
        self.assertEqual(self._err("Please sign in to view this content").status_key, "login_required")

    def test_maps_cannot_parse_data_message(self) -> None:
        self.assertEqual(self._err("Cannot parse data").status_key, "extractor_changed")

    def test_maps_requested_format_not_available_message(self) -> None:
        self.assertEqual(self._err("requested format is not available").status_key, "unsupported_codec")

    def test_falls_back_to_download_failed_with_error_detail(self) -> None:
        error = self._err("Some completely unknown error occurred")
        self.assertEqual(error.status_key, "download_failed")
        self.assertIn("error", error.data)


class RejectByDurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyDownloadService()

    def test_accepts_video_within_max_seconds(self) -> None:
        self.assertIsNone(self.service.reject_by_duration({"duration": 60}, max_seconds=180))

    def test_rejects_video_exceeding_max_seconds(self) -> None:
        self.assertIsNotNone(self.service.reject_by_duration({"duration": 181}, max_seconds=180))

    def test_accepts_video_meeting_min_seconds_threshold(self) -> None:
        self.assertIsNone(self.service.reject_by_duration({"duration": 181}, min_seconds=181))

    def test_rejects_video_below_min_seconds(self) -> None:
        self.assertIsNotNone(self.service.reject_by_duration({"duration": 60}, min_seconds=181))

    def test_accepts_video_with_no_duration_field(self) -> None:
        self.assertIsNone(self.service.reject_by_duration({}, min_seconds=181))

    def test_accepts_video_when_no_filter_constraints_given(self) -> None:
        self.assertIsNone(self.service.reject_by_duration({"duration": 9999}))

    def test_build_match_filter_returns_none_when_filter_is_all(self) -> None:
        self.assertIsNone(self.service.build_match_filter("all"))

    def test_build_match_filter_short_rejects_long_videos(self) -> None:
        f = self.service.build_match_filter("short")
        self.assertIsNone(f({"duration": 60}))
        self.assertIsNotNone(f({"duration": 200}))

    def test_build_match_filter_long_rejects_short_videos(self) -> None:
        f = self.service.build_match_filter("long")
        self.assertIsNone(f({"duration": 300}))
        self.assertIsNotNone(f({"duration": 60}))


if __name__ == "__main__":
    unittest.main()
