from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.platforms.common import UserFacingDownloadError
from app.platforms.douyin.service import CONFIG, DouyinService


class DouyinServiceTest(unittest.TestCase):
    def test_single_download_accepts_and_normalizes_douyin_video_urls(self) -> None:
        service = DouyinService()
        user_modal_url = (
            "https://www.douyin.com/user/MS4wLjABAAAAIfBd_aMnXBXW4C7uoyFQF6oYA16WEG7s-3-Wci_8mUU"
            "ljVuA_7QnoZGquKLbfI6g?modal_id=7546503572885343545"
        )
        shared_text = (
            "2.53 06/10 tRK:/ N@j.pQ :8pm 音乐基础 #卡点 "
            "https://v.douyin.com/WuhW0n1HDig/ 复制此链接，打开Dou音搜索，直接观看视频！"
        )

        shared_text_with_symbols = (
            "5.69 DHV:/ :9pm N@W.zg 01/01 辣妹天菜！灰色正肩T+牛仔裤太辣了 "
            "https://v.douyin.com/-t_Jdp1IRt8/ 复制此链接，打开Dou音搜索，直接观看视频！"
        )

        shared_text_with_plain_short_code = (
            "2.00 H@V.LW :9pm HVy:/ 11/22 想念在清迈游泳的每一天 # 明媚 # 泳池拍照 "
            "https://v.douyin.com/mQdLvYHuZks/ 复制此链接，打开Dou音搜索，直接观看视频！"
        )

        self.assertTrue(service.is_supported_video_url("https://www.douyin.com/video/6961737553342991651"))
        self.assertTrue(service.is_supported_video_url("https://www.iesdouyin.com/share/video/6961737553342991651/"))
        self.assertEqual(
            service.normalize_video_url("https://www.iesdouyin.com/share/video/6961737553342991651/?region=CN"),
            "https://www.douyin.com/video/6961737553342991651",
        )
        self.assertEqual(
            service.normalize_video_url(user_modal_url),
            "https://www.douyin.com/video/7546503572885343545",
        )
        with patch.object(service, "resolve_douyin_redirect", return_value=user_modal_url) as resolve:
            self.assertEqual(
                service.normalize_video_url(shared_text),
                "https://www.douyin.com/video/7546503572885343545",
            )
        resolve.assert_called_once_with("https://v.douyin.com/WuhW0n1HDig/")
        with patch.object(service, "resolve_douyin_redirect", return_value=user_modal_url) as resolve:
            self.assertEqual(
                service.extract_douyin_url(shared_text_with_symbols),
                "https://v.douyin.com/-t_Jdp1IRt8/",
            )
            self.assertEqual(
                service.clean_input_url(shared_text_with_symbols),
                "https://v.douyin.com/-t_Jdp1IRt8/",
            )
            self.assertEqual(
                service.normalize_video_url(shared_text_with_symbols),
                "https://www.douyin.com/video/7546503572885343545",
            )
        resolve.assert_called_once_with("https://v.douyin.com/-t_Jdp1IRt8/")
        self.assertEqual(
            service.clean_input_url(shared_text_with_plain_short_code),
            "https://v.douyin.com/mQdLvYHuZks/",
        )

        with self.assertRaises(UserFacingDownloadError) as context:
            service.download_single("https://www.douyin.com/user/SEC_UID", "downloads", lambda *args: None)

        self.assertEqual(context.exception.status_key, "douyin_single_link")

    def test_page_download_collects_profile_urls_without_duration_filter(self) -> None:
        service = DouyinService()
        progress = lambda *args: None
        urls = ["https://www.douyin.com/video/6961737553342991651"]
        profile_text = (
            "Profile: https://www.douyin.com/user/SEC_UID"
            "?modal_id=7645222203525247345&showSubTab=video&showTab=post"
        )

        with patch.object(service, "collect_profile_video_urls", return_value=urls) as collect, patch.object(
            service, "download_urls"
        ) as download_urls:
            service.download_page(profile_text, "downloads", progress, page_filter="short")

        collect.assert_called_once_with(profile_text, progress)
        download_urls.assert_called_once_with(
            urls,
            "downloads",
            progress,
            single=False,
            page_filter="all",
            emit_initial_progress=False,
        )
        self.assertFalse(CONFIG.supports_page_filters)
        self.assertFalse(CONFIG.supports_analysis)
        self.assertFalse(CONFIG.supports_manual_cookies)

    def test_collects_profile_video_urls_with_api_pagination(self) -> None:
        service = DouyinService()
        profile_text = (
            "copy: https://www.douyin.com/user/SEC_UID"
            "?modal_id=7645222203525247345&showSubTab=video&showTab=post"
        )
        first_page = {
            "aweme_list": [
                {"aweme_id": "1111111111111111111", "video": {}},
                {"aweme_id": "2222222222222222222", "images": [{"url": "https://example.com/a.jpg"}]},
            ],
            "has_more": 1,
            "max_cursor": "cursor-1",
        }
        second_page = {
            "aweme_list": [{"aweme_id": "3333333333333333333", "video": {"play_addr": {}}}],
            "has_more": 0,
        }

        with patch.object(service, "fetch_profile_json", side_effect=[first_page, second_page]):
            urls = service.collect_profile_video_urls(profile_text)

        self.assertEqual(
            urls,
            [
                "https://www.douyin.com/video/1111111111111111111",
                "https://www.douyin.com/video/3333333333333333333",
            ],
        )

    def test_collects_profile_video_urls_from_html_when_api_fails(self) -> None:
        service = DouyinService()
        page_html = r"""
            <a href="https:\/\/www.douyin.com\/video\/1111111111111111111">one</a>
            {"awemeId":"2222222222222222222"}
            https://www.douyin.com/video/1111111111111111111
        """

        with patch.object(
            service,
            "fetch_profile_json",
            side_effect=UserFacingDownloadError("douyin_profile_failed"),
        ), patch.object(service, "fetch_profile_html", return_value=page_html):
            urls = service.collect_profile_video_urls("https://www.douyin.com/user/SEC_UID")

        self.assertEqual(
            urls,
            [
                "https://www.douyin.com/video/1111111111111111111",
                "https://www.douyin.com/video/2222222222222222222",
            ],
        )

    def test_collects_profile_video_urls_after_resolving_short_link(self) -> None:
        service = DouyinService()
        short_text = "https://v.douyin.com/WuhW0n1HDig/ 复制此链接"
        resolved_url = (
            "https://www.douyin.com/user/SEC_UID"
            "?modal_id=7546503572885343545&showSubTab=video&showTab=post"
        )
        page = {
            "aweme_list": [{"aweme_id": "1111111111111111111", "video": {}}],
            "has_more": 0,
        }

        with patch.object(service, "resolve_douyin_redirect", return_value=resolved_url) as resolve, patch.object(
            service, "fetch_profile_json", return_value=page
        ):
            urls = service.collect_profile_video_urls(short_text)

        resolve.assert_called_once_with("https://v.douyin.com/WuhW0n1HDig/")
        self.assertEqual(urls, ["https://www.douyin.com/video/1111111111111111111"])

    def test_rejects_invalid_douyin_profile_pages(self) -> None:
        service = DouyinService()

        with self.assertRaises(UserFacingDownloadError) as context:
            service.collect_profile_video_urls("https://www.douyin.com/video/6961737553342991651")

        self.assertEqual(context.exception.status_key, "douyin_page_link")

        with self.assertRaises(UserFacingDownloadError) as self_context:
            service.collect_profile_video_urls(
                "https://www.douyin.com/user/self?from_tab_name=main&modal_id=7146498192262139148&showTab=like"
            )
        self.assertEqual(self_context.exception.status_key, "douyin_page_link")

    def test_profile_api_does_not_send_cookie_header(self) -> None:
        service = DouyinService()
        captured_requests = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"aweme_list": [], "has_more": 0}'

        def fake_urlopen(request, timeout):
            captured_requests.append(request)
            self.assertEqual(timeout, 25)
            return FakeResponse()

        with patch("app.platforms.douyin.service.urlopen", fake_urlopen):
            response = service.fetch_profile_json("SEC_UID", "0", "https://www.douyin.com/user/SEC_UID")

        request = captured_requests[0]
        params = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(params["sec_user_id"], ["SEC_UID"])
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(response["aweme_list"], [])

    def test_fetches_public_share_video_item_without_cookies(self) -> None:
        service = DouyinService()
        captured_requests = []
        page_html = """
            <script>window._ROUTER_DATA = {"loaderData":{"video_(id)/page":{"videoInfoRes":{"item_list":[
                {"aweme_id":"123456789","video":{"play_addr":{"url_list":["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v1"]}}}
            ]}}},"errors":null}</script>
        """

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return page_html.encode("utf-8")

        def fake_urlopen(request, timeout):
            captured_requests.append(request)
            self.assertEqual(timeout, 25)
            return FakeResponse()

        with patch("app.platforms.douyin.service.urlopen", fake_urlopen):
            item = service.fetch_share_video_item("123456789")

        self.assertIn("/share/video/123456789/", captured_requests[0].full_url)
        self.assertIsNone(captured_requests[0].get_header("Cookie"))
        self.assertEqual(item["aweme_id"], "123456789")

    def test_builds_direct_video_info_from_public_share_data(self) -> None:
        service = DouyinService()
        item = {
            "aweme_id": "123456789",
            "desc": "Public Douyin video",
            "create_time": 1710000000,
            "author": {"nickname": "Creator", "short_id": "creator-id", "sec_uid": "SEC_UID"},
            "video": {
                "duration": 12345,
                "width": 1080,
                "height": 1920,
                "cover": {"url_list": ["https://example.com/cover.webp"]},
                "play_addr": {
                    "url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v1&ratio=720p"]
                },
            },
            "statistics": {"play_count": 10, "digg_count": 3, "comment_count": 2, "share_count": 1},
            "text_extra": [{"hashtag_name": "tag"}],
        }

        info = service.build_video_info(item, "fallback")

        self.assertEqual(info["id"], "123456789")
        self.assertEqual(info["title"], "Public Douyin video")
        self.assertEqual(info["uploader"], "Creator")
        self.assertEqual(info["duration"], 12.345)
        self.assertEqual(info["thumbnail"], "https://example.com/cover.webp")
        self.assertEqual(info["tags"], ["tag"])
        self.assertEqual(info["formats"][0]["url"], "https://aweme.snssdk.com/aweme/v1/play/?video_id=v1&ratio=720p")
        self.assertEqual(info["formats"][1]["url"], "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v1&ratio=720p")
        self.assertGreater(info["formats"][0]["preference"], info["formats"][1]["preference"])
        self.assertGreater(info["formats"][0]["quality"], info["formats"][1]["quality"])
        self.assertEqual(info["formats"][0]["vcodec"], "h264")
        self.assertEqual(info["formats"][0]["acodec"], "aac")

    def test_options_follow_tiktok_like_mp4_policy_without_cookies(self) -> None:
        service = DouyinService()

        with patch.object(service, "has_ffmpeg", return_value=True):
            options = service.build_yt_dlp_options("downloads", lambda info: None, single=False)

        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertEqual(options["ignoreerrors"], "only_download")
        self.assertTrue(options["noplaylist"])
        self.assertNotIn("cookiefile", options)
        self.assertNotIn("cookiesfrombrowser", options)
        self.assertIn("b[ext=mp4][vcodec=h264][acodec=aac]", options["format"])
        self.assertIn("bv*[ext=mp4][vcodec=h264]+ba[ext=m4a]", options["format"])

        match_filter = options["match_filter"]
        self.assertIsNone(match_filter({"formats": [{"ext": "mp4", "vcodec": "h264", "acodec": "aac"}]}))
        self.assertEqual(
            match_filter({"formats": [{"ext": "jpg", "vcodec": "none", "acodec": "none"}]}),
            "Skipped Douyin image/slideshow post",
        )
        with YoutubeDL(options):
            pass

    def test_maps_fresh_cookie_error_to_non_cookie_douyin_message(self) -> None:
        service = DouyinService()

        error = service.to_user_error(DownloadError("ERROR: [Douyin] 123: Fresh cookies are needed"))

        self.assertEqual(error.status_key, "douyin_extract_failed")

    def test_dpapi_cookie_errors_are_not_part_of_douyin_flow(self) -> None:
        service = DouyinService()

        error = service.to_user_error(DownloadError("ERROR: Failed to decrypt with DPAPI"))

        self.assertEqual(error.status_key, "download_failed")

        copy_error = service.to_user_error(DownloadError("ERROR: Could not copy Chrome cookie database"))

        self.assertEqual(copy_error.status_key, "download_failed")


if __name__ == "__main__":
    unittest.main()
