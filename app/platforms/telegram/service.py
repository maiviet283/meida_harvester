from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from app.platforms.common import PlatformConfig, ProgressCallback, UserFacingDownloadError


CONFIG = PlatformConfig(
    key="telegram",
    example_video_url="https://t.me/channel_name/123",
    example_page_url="https://t.me/channel_name",
)

_APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ClipFlow"
_SESSION_FILE = _APP_DIR / "telegram.session"

# Credentials của Telegram Desktop (public, dùng để test)
# Trước khi release: đổi sang credentials đăng ký riêng tại my.telegram.org
_APP_API_ID: int = 2040
_APP_API_HASH: str = "b18441a1ff607e10a989891a5462e627"

_CHUNK_SIZE = 512 * 1024   # 512KB mỗi chunk (default Telethon là 128KB)


class TelegramService:
    def __init__(self) -> None:
        self.cancel_requested = False
        self._pending_2fa_session: str = ""

    def request_cancel(self) -> None:
        self.cancel_requested = True

    def is_cancel_requested(self) -> bool:
        return self.cancel_requested

    def get_api_credentials(self) -> tuple[int, str]:
        return _APP_API_ID, _APP_API_HASH

    def get_session_string(self) -> str:
        try:
            return _SESSION_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def save_session_string(self, session_string: str) -> None:
        _APP_DIR.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(session_string, encoding="utf-8")

    def clear_session(self) -> None:
        _SESSION_FILE.unlink(missing_ok=True)
        self._pending_2fa_session = ""

    def is_authenticated(self) -> bool:
        return bool(self.get_session_string())

    def send_otp(self, phone: str) -> tuple[str, str]:
        api_id, api_hash = self.get_api_credentials()
        return self._run(self._send_otp_async(api_id, api_hash, phone.strip()))

    def verify_otp(self, phone: str, phone_code_hash: str, code: str, partial_session: str) -> None:
        api_id, api_hash = self.get_api_credentials()
        self._run(self._verify_otp_async(api_id, api_hash, phone, phone_code_hash, code, partial_session))

    def verify_2fa(self, password: str) -> None:
        api_id, api_hash = self.get_api_credentials()
        self._run(self._verify_2fa_async(api_id, api_hash, password))

    def get_me(self) -> dict | None:
        try:
            return self._run(self._get_me_async())
        except Exception:
            return None

    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        self.cancel_requested = False
        self._run(self._download_single_async(url, folder, progress))

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "all",
    ) -> None:
        self.cancel_requested = False
        self._run(self._download_page_async(url, folder, progress))

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    async def _make_client(self):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_id, api_hash = self.get_api_credentials()
        session_string = self.get_session_string()
        if not session_string:
            raise UserFacingDownloadError("telegram_not_authenticated")
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            raise UserFacingDownloadError("telegram_not_authenticated")
        return client

    async def _send_otp_async(self, api_id: int, api_hash: str, phone: str) -> tuple[str, str]:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), api_id, api_hash)
        try:
            await client.connect()
            result = await client.send_code_request(phone)
            return client.session.save(), result.phone_code_hash
        except UserFacingDownloadError:
            raise
        except Exception as exc:
            raise UserFacingDownloadError("telegram_otp_failed", {"error": str(exc)}) from exc
        finally:
            await client.disconnect()

    async def _verify_otp_async(
        self,
        api_id: int,
        api_hash: str,
        phone: str,
        phone_code_hash: str,
        code: str,
        partial_session: str,
    ) -> None:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(partial_session), api_id, api_hash)
        try:
            await client.connect()
            try:
                await client.sign_in(phone, code.strip(), phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                self._pending_2fa_session = client.session.save()
                raise UserFacingDownloadError("telegram_2fa_required")
            self.save_session_string(client.session.save())
        except UserFacingDownloadError:
            raise
        except Exception as exc:
            raise UserFacingDownloadError("telegram_verify_failed", {"error": str(exc)}) from exc
        finally:
            await client.disconnect()

    async def _verify_2fa_async(self, api_id: int, api_hash: str, password: str) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(self._pending_2fa_session), api_id, api_hash)
        try:
            await client.connect()
            try:
                await client.sign_in(password=password)
            except Exception as exc:
                raise UserFacingDownloadError("telegram_2fa_failed", {"error": str(exc)}) from exc
            self.save_session_string(client.session.save())
            self._pending_2fa_session = ""
        except UserFacingDownloadError:
            raise
        finally:
            await client.disconnect()

    async def _get_me_async(self) -> dict | None:
        client = await self._make_client()
        try:
            me = await client.get_me()
            if not me:
                return None
            return {
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "username": me.username or "",
                "phone": me.phone or "",
            }
        finally:
            await client.disconnect()

    async def _download_single_async(self, url: str, folder: str, progress: ProgressCallback) -> None:
        from telethon.errors import FloodWaitError
        from telethon.tl.types import PeerChannel

        progress("preparing", 8, None)
        Path(folder).mkdir(parents=True, exist_ok=True)

        entity_ref, msg_id = self._parse_single_url(url)
        if msg_id is None:
            raise UserFacingDownloadError("telegram_single_link")

        client = await self._make_client()
        try:
            progress("reading", 18, None)
            entity = await client.get_entity(
                PeerChannel(entity_ref) if isinstance(entity_ref, int) else entity_ref
            )
            message = await client.get_messages(entity, ids=msg_id)
            if not message or not self._has_video(message):
                raise UserFacingDownloadError("telegram_no_video")

            channel_name = self._entity_name(entity)
            progress("downloading", 20, None)
            await self._fast_download(
                client,
                message,
                folder,
                channel_name,
                lambda recv, total: progress("downloading", 20 + min(int(recv * 78 / total), 78), None) if total else None,
            )

            if self.cancel_requested:
                raise UserFacingDownloadError("download_cancelled")
        except UserFacingDownloadError:
            raise
        except FloodWaitError as exc:
            raise UserFacingDownloadError("telegram_flood_wait", {"seconds": str(exc.seconds)}) from exc
        except Exception as exc:
            raise UserFacingDownloadError("download_failed", {"error": str(exc)}) from exc
        finally:
            await client.disconnect()

        progress("finished", 100, None)

    async def _download_page_async(self, url: str, folder: str, progress: ProgressCallback) -> None:
        from telethon.errors import FloodWaitError
        from telethon.tl.types import PeerChannel

        progress("preparing", 8, None)
        Path(folder).mkdir(parents=True, exist_ok=True)

        entity_ref = self._parse_page_url(url)
        if entity_ref is None:
            raise UserFacingDownloadError("telegram_page_link")

        client = await self._make_client()
        try:
            progress("reading", 15, None)
            entity = await client.get_entity(
                PeerChannel(entity_ref) if isinstance(entity_ref, int) else entity_ref
            )

            channel_name = self._entity_name(entity)
            downloaded = 0
            async for message in client.iter_messages(entity):
                if self.cancel_requested:
                    raise UserFacingDownloadError("download_cancelled")
                if not self._has_video(message):
                    continue

                downloaded += 1
                base = 20 + min(downloaded, 75)
                progress("telegram_downloading", base, {"current": str(downloaded)})

                await self._fast_download(
                    client,
                    message,
                    folder,
                    channel_name,
                    lambda recv, total, _b=base: (
                        progress("telegram_downloading", _b, {"current": str(downloaded)}) if total else None
                    ),
                )

            if downloaded == 0:
                raise UserFacingDownloadError("telegram_page_no_videos")
            if self.cancel_requested:
                raise UserFacingDownloadError("download_cancelled")
        except UserFacingDownloadError:
            raise
        except FloodWaitError as exc:
            raise UserFacingDownloadError("telegram_flood_wait", {"seconds": str(exc.seconds)}) from exc
        except Exception as exc:
            raise UserFacingDownloadError("download_failed", {"error": str(exc)}) from exc
        finally:
            await client.disconnect()

        progress("finished", 100, None)

    async def _fast_download(
        self,
        client,
        message,
        output_folder: str,
        channel_name: str,
        progress_cb=None,
    ) -> None:
        from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument

        doc = None
        if message.video:
            doc = message.video
        elif isinstance(message.media, MessageMediaDocument):
            raw = message.media.document
            if hasattr(raw, "size"):
                doc = raw

        if not doc:
            raise UserFacingDownloadError("telegram_no_video")

        ext = "mp4"
        doc_filename = f"{doc.id}.mp4"
        for attr in doc.attributes or []:
            if isinstance(attr, DocumentAttributeFilename):
                doc_filename = attr.file_name
                ext = Path(attr.file_name).suffix.lstrip(".") or "mp4"
                break

        caption = (message.text or "").strip()
        base_name = self._sanitize(caption[:80]) if caption else self._sanitize(Path(doc_filename).stem[:80])
        if not base_name:
            base_name = str(doc.id)

        filename = f"{base_name} [{message.id}].{ext}"
        save_dir = Path(output_folder) / "Telegram" / self._sanitize(channel_name)
        save_dir.mkdir(parents=True, exist_ok=True)
        output_path = save_dir / filename

        file_size: int = doc.size
        received = 0

        with open(output_path, "wb") as f:
            async for chunk in client.iter_download(message.media, chunk_size=_CHUNK_SIZE):
                if self.cancel_requested:
                    break
                f.write(chunk)
                received += len(chunk)
                if progress_cb:
                    progress_cb(received, file_size)

    def _entity_name(self, entity) -> str:
        if hasattr(entity, "title") and entity.title:
            return entity.title
        if hasattr(entity, "username") and entity.username:
            return entity.username
        if hasattr(entity, "first_name"):
            parts = [entity.first_name or "", getattr(entity, "last_name", "") or ""]
            name = " ".join(p for p in parts if p).strip()
            if name:
                return name
        return str(getattr(entity, "id", "telegram"))

    def _sanitize(self, name: str) -> str:
        for ch in r'\/:*?"<>|':
            name = name.replace(ch, "_")
        return name.strip().strip(".")[:100] or "telegram"

    def _has_video(self, message) -> bool:
        from telethon.tl.types import MessageMediaDocument

        if not message or not message.media:
            return False
        if message.video:
            return True
        if isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc and hasattr(doc, "mime_type"):
                return doc.mime_type.startswith("video/")
        return False

    def _parse_single_url(self, url: str) -> tuple[str | int, int | None]:
        url = url.strip()
        m = re.match(r"(?:https?://)?t\.me/c/(\d+)/(\d+)", url)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.match(r"(?:https?://)?t\.me/([^/?#]+)/(\d+)", url)
        if m:
            return m.group(1), int(m.group(2))
        return url, None

    def _parse_page_url(self, url: str) -> str | int | None:
        url = url.strip()
        m = re.match(r"(?:https?://)?t\.me/c/(\d+)(?:/\d+)?/?$", url)
        if m:
            return int(m.group(1))
        m = re.match(r"(?:https?://)?t\.me/([^/?#]+)/?$", url)
        if m and not m.group(1).startswith("+"):
            return m.group(1)
        return None

    def clean_input_url(self, url: str, mode: str = "") -> str:
        return url.strip()
