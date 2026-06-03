from __future__ import annotations

import json
import socket
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from app.platforms.drive.service import CONFIG, DriveDownloadService, _IPv4HTTPSConnection
from app.platforms.common import UserFacingDownloadError


def _video_info_body(player_response: dict, status: str = "ok", **extra: str) -> str:
    fields = {"status": status, "title": "15. BÀI 10 MÔ TẢ CĂN NHÀ.mp4(DRIVE 5.0)", "length_seconds": "1204"}
    fields.update(extra)
    fields["player_response"] = json.dumps(player_response)
    return urllib.parse.urlencode(fields)


_PLAYER_RESPONSE = {
    "streamingData": {
        "formats": [
            {"itag": 18, "url": "https://video.example/itag18", "mimeType": 'video/mp4; codecs="avc1.4D001F"',
             "width": 640, "height": 360, "bitrate": 800000, "contentLength": "1000"},
            {"itag": 37, "url": "https://video.example/itag37", "mimeType": 'video/mp4; codecs="avc1.640032"',
             "width": 1920, "height": 1080, "bitrate": 4000000, "contentLength": "9000"},
        ],
        "adaptiveFormats": [
            {"itag": 137, "url": "https://video.example/itag137", "mimeType": 'video/mp4; codecs="avc1.42C028"',
             "width": 1920, "height": 1080, "bitrate": 3500000},
            {"itag": 140, "url": "https://video.example/itag140", "mimeType": 'audio/mp4; codecs="mp4a.40.2"',
             "bitrate": 128000},
        ],
    },
}


class DriveServiceTest(unittest.TestCase):
    def test_single_download_normalizes_file_links_before_fetch(self) -> None:
        service = DriveDownloadService()
        progress = lambda *args: None

        with patch.object(service, "_download_one") as download_one:
            service.download_single("https://drive.google.com/file/d/FILE_ID/view?usp=sharing", "downloads", progress)

        download_one.assert_called_once_with("https://drive.google.com/file/d/FILE_ID/view", "downloads", progress)

    def test_accepts_common_drive_file_url_shapes(self) -> None:
        service = DriveDownloadService()

        self.assertEqual(
            service.clean_input_url("https://drive.google.com/open?id=FILE_ID", "single"),
            "https://drive.google.com/file/d/FILE_ID/view",
        )
        self.assertEqual(
            service.clean_input_url("https://drive.google.com/uc?export=download&id=FILE_ID", "single"),
            "https://drive.google.com/file/d/FILE_ID/view",
        )
        self.assertEqual(
            service.clean_input_url("https://drive.usercontent.google.com/download?id=FILE_ID&export=download", "single"),
            "https://drive.google.com/file/d/FILE_ID/view",
        )
        self.assertEqual(
            service.clean_input_url("https://drive.google.com/file/d/FILE_ID/view?resourcekey=0-secret", "single"),
            "https://drive.google.com/file/d/FILE_ID/view?resourcekey=0-secret",
        )
        self.assertEqual(
            service.clean_input_url("https://drive.google.com/drive/folders/FOLDER_ID?resourcekey=0-folder", "page"),
            "https://drive.google.com/drive/folders/FOLDER_ID?resourcekey=0-folder",
        )

    def test_fetch_metadata_parses_video_info_response(self) -> None:
        service = DriveDownloadService()

        with patch.object(service, "_fetch_video_info", return_value=_video_info_body(_PLAYER_RESPONSE)):
            info = service._fetch_video_metadata("FILE_ID", "https://drive.google.com/file/d/FILE_ID/view")

        self.assertEqual(info["id"], "FILE_ID")
        self.assertEqual(info["title"], "15. BÀI 10 MÔ TẢ CĂN NHÀ")
        self.assertEqual(info["duration"], 1204)
        ids = {fmt["format_id"] for fmt in info["formats"]}
        self.assertEqual(ids, {"18", "37", "137", "140"})

    def test_progressive_format_has_video_and_audio_codecs(self) -> None:
        service = DriveDownloadService()
        with patch.object(service, "_fetch_video_info", return_value=_video_info_body(_PLAYER_RESPONSE)):
            formats = {f["format_id"]: f for f in service._fetch_video_metadata("ID", "url")["formats"]}

        self.assertEqual(formats["37"]["vcodec"], "avc1.640032")
        self.assertEqual(formats["37"]["acodec"], "mp4a.40.2")
        self.assertEqual(formats["137"]["acodec"], "none")
        self.assertEqual(formats["140"]["vcodec"], "none")
        self.assertEqual(formats["140"]["ext"], "m4a")

    def test_video_info_failure_maps_to_status_keys(self) -> None:
        service = DriveDownloadService()

        cases = {
            "DISABLED_BY_CONTENT_OWNER no permission": "drive_permission_denied",
            "TOO_MANY_REQUESTS limit": "drive_quota_exceeded",
            "UNKNOWN": "drive_no_video",
        }
        for reason, expected in cases.items():
            with patch.object(service, "_fetch_video_info", return_value=_video_info_body({}, status="fail", reason=reason)):
                with self.assertRaises(UserFacingDownloadError) as context:
                    service._fetch_video_metadata("ID", "url")
            self.assertEqual(context.exception.status_key, expected)

    def test_select_formats_prefers_best_progressive(self) -> None:
        service = DriveDownloadService()
        with patch.object(service, "_fetch_video_info", return_value=_video_info_body(_PLAYER_RESPONSE)):
            formats = service._fetch_video_metadata("ID", "url")["formats"]

        progressive, video, audio = service._select_formats(formats)
        self.assertEqual(progressive["format_id"], "37")
        self.assertEqual(video["format_id"], "137")
        self.assertEqual(audio["format_id"], "140")

    def test_download_streams_uses_single_progressive_file(self) -> None:
        service = DriveDownloadService()
        info = {"id": "ID", "title": "Clip", "formats": [
            {"format_id": "37", "url": "https://video.example/itag37", "ext": "mp4",
             "height": 1080, "vcodec": "avc1", "acodec": "mp4a"},
        ]}
        calls: list[tuple[str, str]] = []

        def fake_download(url, path, progress, start, end):
            calls.append((url, path.name))

        with patch.object(service, "_download_to_file", side_effect=fake_download):
            service._download_streams(info, "downloads", lambda *a: None)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://video.example/itag37")
        self.assertTrue(calls[0][1].endswith(".mp4"))

    def test_download_streams_merges_adaptive_when_ffmpeg_available(self) -> None:
        service = DriveDownloadService()
        info = {"id": "ID", "title": "Clip", "formats": [
            {"format_id": "137", "url": "https://video.example/v", "ext": "mp4", "height": 1080,
             "vcodec": "avc1", "acodec": "none"},
            {"format_id": "140", "url": "https://video.example/a", "ext": "m4a",
             "vcodec": "none", "acodec": "mp4a"},
        ]}
        downloaded: list[str] = []
        merged: list[str] = []

        with patch.object(service, "has_ffmpeg", return_value=True), patch.object(
            service, "_download_to_file", side_effect=lambda url, *a: downloaded.append(url)
        ), patch.object(service, "_merge_streams", side_effect=lambda *a: merged.append("merged")):
            service._download_streams(info, "downloads", lambda *a: None)

        self.assertEqual(downloaded, ["https://video.example/v", "https://video.example/a"])
        self.assertEqual(merged, ["merged"])

    def test_download_streams_without_formats_raises(self) -> None:
        service = DriveDownloadService()
        with self.assertRaises(UserFacingDownloadError) as context:
            service._download_streams({"id": "ID", "title": "Clip", "formats": []}, "downloads", lambda *a: None)
        self.assertEqual(context.exception.status_key, "drive_no_video")

    def test_page_download_continues_after_failed_files(self) -> None:
        service = DriveDownloadService()
        calls: list[str] = []

        def fake_download_one(file_url, folder, progress):
            calls.append(file_url)
            if len(calls) == 1:
                raise UserFacingDownloadError("drive_permission_denied")

        with patch.object(service, "_list_folder", return_value=[
            "https://drive.google.com/file/d/FIRST/view",
            "https://drive.google.com/file/d/SECOND/view",
        ]), patch.object(service, "_download_one", side_effect=fake_download_one):
            service.download_page("https://drive.google.com/drive/folders/FOLDER_ID", "downloads", lambda *a: None)

        self.assertEqual(calls, [
            "https://drive.google.com/file/d/FIRST/view",
            "https://drive.google.com/file/d/SECOND/view",
        ])

    def test_page_download_does_not_swallow_cancel(self) -> None:
        service = DriveDownloadService()

        def fake_download_one(file_url, folder, progress):
            raise UserFacingDownloadError("download_cancelled")

        with patch.object(service, "_list_folder", return_value=["https://drive.google.com/file/d/FIRST/view"]), patch.object(
            service, "_download_one", side_effect=fake_download_one
        ):
            with self.assertRaises(UserFacingDownloadError) as context:
                service.download_page("https://drive.google.com/drive/folders/FOLDER_ID", "downloads", lambda *a: None)

        self.assertEqual(context.exception.status_key, "download_cancelled")

    def test_page_download_all_failed_raises(self) -> None:
        service = DriveDownloadService()
        with patch.object(service, "_list_folder", return_value=["https://drive.google.com/file/d/A/view"]), patch.object(
            service, "_download_one", side_effect=UserFacingDownloadError("drive_no_video")
        ):
            with self.assertRaises(UserFacingDownloadError) as context:
                service.download_page("https://drive.google.com/drive/folders/FOLDER_ID", "downloads", lambda *a: None)
        self.assertEqual(context.exception.status_key, "drive_all_failed")

    def test_clean_video_title_strips_drive_suffix_and_extension(self) -> None:
        self.assertEqual(
            DriveDownloadService._clean_video_title("15. BÀI 10.mp4(DRIVE 5.0)"),
            "15. BÀI 10",
        )

    def test_output_basename_sanitizes_illegal_characters(self) -> None:
        name = DriveDownloadService._build_output_basename('a/b:c*?"<>|d', "FILE_ID")
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertTrue(name.endswith("[FILE_ID]"))

    def test_extracts_folder_file_ids_from_drive_html(self) -> None:
        html = r'''
            <a href="/file/d/111111111111111111111111111/view">one</a>
            <div data-id="222222222222222222222222222"></div>
            ["333333333333333333333333333","clip.mp4"]
            <a href="/file/d/444444444444444444444444444/view?resourcekey=0-secret">protected</a>
            <a href="/file/d/111111111111111111111111111/view">duplicate</a>
        '''

        self.assertEqual(
            DriveDownloadService._extract_file_ids_from_html(html),
            [
                "222222222222222222222222222",
                "111111111111111111111111111",
                "444444444444444444444444444",
                "333333333333333333333333333",
            ],
        )
        file_urls = DriveDownloadService._extract_file_urls_from_html(html)
        self.assertIn(
            "https://drive.google.com/file/d/444444444444444444444444444/view?resourcekey=0-secret",
            file_urls,
        )

    def test_ipv4_connection_binds_to_ipv4_source_address(self) -> None:
        connection = _IPv4HTTPSConnection("drive.google.com")
        self.assertEqual(connection.source_address, ("0.0.0.0", 0))
        self.assertEqual(socket.AF_INET if ":" not in connection.source_address[0] else socket.AF_INET6, socket.AF_INET)

    def test_config_supports_manual_cookies(self) -> None:
        self.assertTrue(CONFIG.supports_manual_cookies)


if __name__ == "__main__":
    unittest.main()
