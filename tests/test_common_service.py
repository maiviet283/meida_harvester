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


if __name__ == "__main__":
    unittest.main()
