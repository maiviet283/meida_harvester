from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.cookie_store import LocalCookieStore


class LocalCookieStoreTest(unittest.TestCase):
    def test_round_trips_protected_cookie_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.json"
            store = LocalCookieStore(
                path,
                protect_secret=lambda value: b"wrapped:" + value,
                unprotect_secret=lambda value: value.removeprefix(b"wrapped:"),
            )

            store.set("instagram", "sessionid=abc; ds_user_id=123")

            self.assertEqual(store.get("instagram"), "sessionid=abc; ds_user_id=123")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["cookies"]["instagram"]["encoding"], "dpapi")
            self.assertNotIn("sessionid=abc", data["cookies"]["instagram"]["value"])

    def test_falls_back_to_plain_file_when_protection_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.json"
            store = LocalCookieStore(path, protect_secret=lambda value: None, unprotect_secret=None)

            store.set("facebook", "c_user=1; xs=2")

            self.assertEqual(store.get("facebook"), "c_user=1; xs=2")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["cookies"]["facebook"], {"encoding": "plain", "value": "c_user=1; xs=2"})

    def test_blank_cookie_clears_existing_value(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.json"
            store = LocalCookieStore(path, protect_secret=lambda value: None, unprotect_secret=None)

            store.set("facebook", "c_user=1")
            store.set("facebook", "")

            self.assertEqual(store.get("facebook"), "")
            self.assertNotIn("facebook", json.loads(path.read_text(encoding="utf-8"))["cookies"])

    def test_invalid_store_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.json"
            path.write_text("{bad json", encoding="utf-8")
            store = LocalCookieStore(path, protect_secret=lambda value: None, unprotect_secret=None)

            self.assertEqual(store.get("instagram"), "")


if __name__ == "__main__":
    unittest.main()
