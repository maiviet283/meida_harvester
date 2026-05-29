from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from ctypes import wintypes
from typing import Callable


SecretCodec = Callable[[bytes], bytes | None]


def default_cookie_store_path() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / "ClipFlow" / "cookies.json"
    return Path.home() / ".clipflow" / "cookies.json"


def _dpapi_protect(data: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    return _dpapi_transform(data, protect=True)


def _dpapi_unprotect(data: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    return _dpapi_transform(data, protect=False)


def _dpapi_transform(data: bytes, protect: bool) -> bytes | None:
    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = DataBlob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob))
    if not ok:
        return None
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


class LocalCookieStore:
    def __init__(
        self,
        path: Path | None = None,
        protect_secret: SecretCodec | None = _dpapi_protect,
        unprotect_secret: SecretCodec | None = _dpapi_unprotect,
    ) -> None:
        self.path = path or default_cookie_store_path()
        self.protect_secret = protect_secret
        self.unprotect_secret = unprotect_secret

    def get(self, platform_key: str) -> str:
        entry = self._load().get("cookies", {}).get(platform_key)
        if isinstance(entry, str):
            return entry
        if not isinstance(entry, dict):
            return ""

        value = str(entry.get("value") or "")
        encoding = entry.get("encoding")
        if encoding == "plain":
            return value
        if encoding != "dpapi" or not self.unprotect_secret:
            return ""

        try:
            encrypted = base64.b64decode(value.encode("ascii"))
        except ValueError:
            return ""
        decrypted = self.unprotect_secret(encrypted)
        if decrypted is None:
            return ""
        return decrypted.decode("utf-8", errors="ignore")

    def set(self, platform_key: str, cookie_header: str) -> None:
        data = self._load()
        cookies = data.setdefault("cookies", {})
        normalized_cookie = cookie_header.strip()
        if not normalized_cookie:
            cookies.pop(platform_key, None)
            self._write(data)
            return

        encoded = self._encode(normalized_cookie)
        cookies[platform_key] = encoded
        self._write(data)

    def _encode(self, value: str) -> dict[str, str]:
        raw = value.encode("utf-8")
        encrypted = self.protect_secret(raw) if self.protect_secret else None
        if encrypted:
            return {
                "encoding": "dpapi",
                "value": base64.b64encode(encrypted).decode("ascii"),
            }
        return {"encoding": "plain", "value": value}

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "cookies": {}}
        if not isinstance(data, dict):
            return {"version": 1, "cookies": {}}
        if not isinstance(data.get("cookies"), dict):
            data["cookies"] = {}
        data.setdefault("version", 1)
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        tmp_path.replace(self.path)


_COOKIE_STORE: LocalCookieStore | None = None


def get_cookie_store() -> LocalCookieStore:
    global _COOKIE_STORE
    if _COOKIE_STORE is None:
        _COOKIE_STORE = LocalCookieStore()
    return _COOKIE_STORE
