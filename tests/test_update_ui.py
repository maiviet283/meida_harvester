from __future__ import annotations

import unittest
from unittest.mock import patch

from app.updater import UpdateCheck, UpdateError, UpdateInfo
import app.update_ui as update_ui


def _info() -> UpdateInfo:
    return UpdateInfo(
        latest_version="1.0.0",
        minimum_supported_version="1.0.0",
        download_url="https://example.com/ClipFlow.zip",
        release_url="https://example.com/releases",
        message="",
    )


class EnsureUpdateAllowedTest(unittest.TestCase):
    def test_fails_open_when_update_check_errors(self) -> None:
        with patch.object(update_ui, "check_for_update", side_effect=UpdateError("server offline")):
            self.assertTrue(update_ui.ensure_update_allowed("vi"))

    def test_allows_when_already_up_to_date(self) -> None:
        check = UpdateCheck(info=_info(), available=False, required=False)
        with patch.object(update_ui, "check_for_update", return_value=check):
            self.assertTrue(update_ui.ensure_update_allowed("vi"))


if __name__ == "__main__":
    unittest.main()
