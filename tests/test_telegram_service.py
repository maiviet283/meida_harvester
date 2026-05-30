from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.platforms.telegram.service import TelegramService


class ParseSingleUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TelegramService()

    def test_parses_public_channel_message_url(self) -> None:
        entity, msg_id = self.service._parse_single_url("https://t.me/channel_name/123")
        self.assertEqual(entity, "channel_name")
        self.assertEqual(msg_id, 123)

    def test_parses_private_channel_message_url(self) -> None:
        entity, msg_id = self.service._parse_single_url("https://t.me/c/1234567890/456")
        self.assertEqual(entity, 1234567890)
        self.assertEqual(msg_id, 456)

    def test_parses_url_without_https_prefix(self) -> None:
        entity, msg_id = self.service._parse_single_url("t.me/channel_name/789")
        self.assertEqual(entity, "channel_name")
        self.assertEqual(msg_id, 789)

    def test_returns_none_msg_id_for_channel_only_url(self) -> None:
        _, msg_id = self.service._parse_single_url("https://t.me/channel_name")
        self.assertIsNone(msg_id)

    def test_returns_none_msg_id_for_unrecognized_url(self) -> None:
        _, msg_id = self.service._parse_single_url("https://example.com/video/123")
        self.assertIsNone(msg_id)


class ParsePageUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TelegramService()

    def test_parses_public_channel_url(self) -> None:
        self.assertEqual(self.service._parse_page_url("https://t.me/channel_name"), "channel_name")

    def test_parses_public_channel_url_with_trailing_slash(self) -> None:
        self.assertEqual(self.service._parse_page_url("https://t.me/channel_name/"), "channel_name")

    def test_parses_private_channel_url(self) -> None:
        self.assertEqual(self.service._parse_page_url("https://t.me/c/1234567890"), 1234567890)

    def test_parses_private_channel_url_with_message_id(self) -> None:
        self.assertEqual(self.service._parse_page_url("https://t.me/c/1234567890/5"), 1234567890)

    def test_parses_url_without_https(self) -> None:
        self.assertEqual(self.service._parse_page_url("t.me/channel_name"), "channel_name")

    def test_returns_none_for_invite_link(self) -> None:
        self.assertIsNone(self.service._parse_page_url("https://t.me/+abcdef123456"))

    def test_returns_none_for_non_telegram_url(self) -> None:
        self.assertIsNone(self.service._parse_page_url("https://example.com/channel"))


class SanitizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TelegramService()

    def test_replaces_windows_invalid_chars(self) -> None:
        result = self.service._sanitize('file:name/with\\bad*chars?"<>|')
        for ch in r'\/:*?"<>|':
            self.assertNotIn(ch, result)

    def test_strips_leading_and_trailing_dots(self) -> None:
        self.assertFalse(self.service._sanitize("...name...").startswith("."))
        self.assertFalse(self.service._sanitize("...name...").endswith("."))

    def test_truncates_long_name_to_100_chars(self) -> None:
        long_name = "a" * 200
        self.assertLessEqual(len(self.service._sanitize(long_name)), 100)

    def test_returns_fallback_for_empty_string(self) -> None:
        self.assertEqual(self.service._sanitize(""), "telegram")

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(self.service._sanitize("  name  "), "name")

    def test_preserves_normal_name(self) -> None:
        self.assertEqual(self.service._sanitize("ClipFlow Channel"), "ClipFlow Channel")


class EntityNameTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TelegramService()

    def test_prefers_title_for_channel(self) -> None:
        entity = MagicMock()
        entity.title = "My Channel"
        entity.username = "mychannel"
        self.assertEqual(self.service._entity_name(entity), "My Channel")

    def test_falls_back_to_username_when_no_title(self) -> None:
        entity = MagicMock(spec=[])
        entity.username = "mychannel"
        self.assertEqual(self.service._entity_name(entity), "mychannel")

    def test_combines_first_and_last_name_for_user(self) -> None:
        entity = MagicMock(spec=["first_name", "last_name", "id"])
        entity.first_name = "Nguyen"
        entity.last_name = "Van A"
        entity.id = 999
        self.assertEqual(self.service._entity_name(entity), "Nguyen Van A")

    def test_handles_missing_last_name(self) -> None:
        entity = MagicMock(spec=["first_name", "id"])
        entity.first_name = "Nguyen"
        entity.id = 999
        self.assertEqual(self.service._entity_name(entity), "Nguyen")

    def test_falls_back_to_entity_id_when_no_name(self) -> None:
        entity = MagicMock(spec=["id"])
        entity.id = 123456
        self.assertIn("123456", self.service._entity_name(entity))


class HasVideoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TelegramService()

    def test_returns_true_when_message_has_video_attribute(self) -> None:
        message = MagicMock()
        message.media = MagicMock()
        message.video = MagicMock()
        self.assertTrue(self.service._has_video(message))

    def test_returns_true_for_document_with_video_mime_type(self) -> None:
        from telethon.tl.types import MessageMediaDocument

        doc = MagicMock()
        doc.mime_type = "video/mp4"
        media = MagicMock(spec=MessageMediaDocument)
        media.document = doc

        message = MagicMock()
        message.media = media
        message.video = None
        self.assertTrue(self.service._has_video(message))

    def test_returns_false_for_document_with_image_mime_type(self) -> None:
        from telethon.tl.types import MessageMediaDocument

        doc = MagicMock()
        doc.mime_type = "image/jpeg"
        media = MagicMock(spec=MessageMediaDocument)
        media.document = doc

        message = MagicMock()
        message.media = media
        message.video = None
        self.assertFalse(self.service._has_video(message))

    def test_returns_false_for_message_without_media(self) -> None:
        message = MagicMock()
        message.media = None
        message.video = None
        self.assertFalse(self.service._has_video(message))

    def test_returns_false_for_none_message(self) -> None:
        self.assertFalse(self.service._has_video(None))


if __name__ == "__main__":
    unittest.main()
