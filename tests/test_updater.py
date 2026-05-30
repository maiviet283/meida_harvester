from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import patch

from app.updater import (
    UpdateError,
    UpdateInfo,
    check_for_update,
    fetch_update_manifest,
    is_version_less,
    parse_version,
)


def _fake_manifest(latest: str, minimum: str | None = None) -> UpdateInfo:
    return UpdateInfo(
        latest_version=latest,
        minimum_supported_version=minimum or latest,
        download_url="https://example.com/ClipFlow.zip",
        release_url="https://example.com/releases",
        message="",
    )


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self) -> bytes:
        return self._body


class ParseVersionTest(unittest.TestCase):
    def test_parses_three_part_semver(self) -> None:
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3))

    def test_strips_leading_v_prefix(self) -> None:
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))

    def test_pads_short_version_to_three_parts(self) -> None:
        self.assertEqual(parse_version("1.0"), (1, 0, 0))

    def test_parses_version_with_build_suffix(self) -> None:
        major, minor, patch_ = parse_version("1.2.3-beta")[:3]
        self.assertEqual((major, minor, patch_), (1, 2, 3))

    def test_parses_zero_version(self) -> None:
        self.assertEqual(parse_version("0.0.0"), (0, 0, 0))

    def test_parses_large_patch_number(self) -> None:
        self.assertEqual(parse_version("0.1.99"), (0, 1, 99))


class IsVersionLessTest(unittest.TestCase):
    def test_older_patch_is_less(self) -> None:
        self.assertTrue(is_version_less("1.0.0", "1.0.1"))

    def test_same_version_is_not_less(self) -> None:
        self.assertFalse(is_version_less("1.0.0", "1.0.0"))

    def test_newer_version_is_not_less(self) -> None:
        self.assertFalse(is_version_less("1.1.0", "1.0.9"))

    def test_minor_version_bump_is_detected(self) -> None:
        self.assertTrue(is_version_less("0.9.99", "1.0.0"))

    def test_patch_comparison_works_both_ways(self) -> None:
        self.assertTrue(is_version_less("1.0.0", "1.0.1"))
        self.assertFalse(is_version_less("1.0.1", "1.0.0"))

    def test_major_version_dominates_minor_and_patch(self) -> None:
        self.assertTrue(is_version_less("0.99.99", "1.0.0"))
        self.assertFalse(is_version_less("2.0.0", "1.99.99"))


class FetchUpdateManifestTest(unittest.TestCase):
    def test_parses_valid_manifest_response(self) -> None:
        payload = {
            "latest_version": "1.2.3",
            "minimum_supported_version": "1.1.0",
            "download_url": "https://example.com/ClipFlow.zip",
            "release_url": "https://example.com/releases",
            "message": "Bug fixes",
        }
        with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(payload)):
            info = fetch_update_manifest("https://example.com/manifest.json")

        self.assertEqual(info.latest_version, "1.2.3")
        self.assertEqual(info.minimum_supported_version, "1.1.0")
        self.assertEqual(info.download_url, "https://example.com/ClipFlow.zip")
        self.assertEqual(info.message, "Bug fixes")

    def test_raises_when_latest_version_field_is_absent(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeHttpResponse({})):
            with self.assertRaises(UpdateError):
                fetch_update_manifest("https://example.com/manifest.json")

    def test_minimum_version_defaults_to_latest_when_absent(self) -> None:
        payload = {"latest_version": "2.0.0"}
        with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(payload)):
            info = fetch_update_manifest("https://example.com/manifest.json")
        self.assertEqual(info.minimum_supported_version, "2.0.0")

    def test_strips_whitespace_from_version_fields(self) -> None:
        payload = {"latest_version": "  1.0.0  ", "minimum_supported_version": " 0.9.0 "}
        with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(payload)):
            info = fetch_update_manifest("https://example.com/manifest.json")
        self.assertEqual(info.latest_version, "1.0.0")
        self.assertEqual(info.minimum_supported_version, "0.9.0")

    def test_raises_update_error_on_network_failure(self) -> None:
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            with self.assertRaises(UpdateError):
                fetch_update_manifest("https://example.com/manifest.json")


class CheckForUpdateTest(unittest.TestCase):
    def test_detects_available_optional_update(self) -> None:
        with patch("app.updater.fetch_update_manifest", return_value=_fake_manifest("1.1.0", "0.9.0")):
            result = check_for_update(current_version="1.0.0")
        self.assertTrue(result.available)
        self.assertFalse(result.required)

    def test_detects_required_forced_update(self) -> None:
        with patch("app.updater.fetch_update_manifest", return_value=_fake_manifest("1.1.0", "1.1.0")):
            result = check_for_update(current_version="1.0.0")
        self.assertTrue(result.required)
        self.assertTrue(result.available)

    def test_no_update_needed_when_version_is_current(self) -> None:
        with patch("app.updater.fetch_update_manifest", return_value=_fake_manifest("1.0.0")):
            result = check_for_update(current_version="1.0.0")
        self.assertFalse(result.available)
        self.assertFalse(result.required)

    def test_available_but_not_required_when_above_minimum(self) -> None:
        with patch("app.updater.fetch_update_manifest", return_value=_fake_manifest("1.2.0", "1.0.0")):
            result = check_for_update(current_version="1.0.5")
        self.assertTrue(result.available)
        self.assertFalse(result.required)


if __name__ == "__main__":
    unittest.main()
