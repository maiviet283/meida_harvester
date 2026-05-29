"""
License validation for ClipFlow.

Luồng:
  1. validate() — gọi khi khởi động
       cache còn hạn  → trả "ok" ngay, không gọi server
       cache hết hạn  → gọi /licenses/validate/, cập nhật cache
       chưa activate  → trả "not_found"
       server offline → gia hạn cache thêm 1h, trả "ok" (grace period)
  2. activate(key) — gọi khi user nhập key lần đầu
       thành công → lưu device_token vào cache, trả "ok"
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import platform
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib import request as _urllib_req
from urllib.error import HTTPError, URLError

_API_BASE = "https://course-finder-3v7y.onrender.com/licenses"
_SIG_KEY = os.environ.get("CF_SIG_KEY", "cf-hmac-default-dev")
_TIMEOUT = 10  # seconds
_CACHE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ClipFlow"
_CACHE_FILE = _CACHE_DIR / "session.dat"

LicenseStatus = Literal[
    "ok",
    "expired",
    "revoked",
    "device_mismatch",
    "not_found",
    "rate_limited",
    "offline",
    "invalid_sig",
    "invalid_request",
]


def get_hwid() -> str:
    node = str(uuid.getnode())
    machine = platform.machine()
    system = platform.system()
    processor = (platform.processor() or "")[:32]
    raw = f"{node}|{machine}|{system}|{processor}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _derive_key(hwid: str) -> bytes:
    return hashlib.sha256(f"cf-session-{hwid}".encode()).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _write_cache(data: dict, hwid: str) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data).encode()
    key = _derive_key(hwid)
    _CACHE_FILE.write_bytes(base64.b64encode(_xor(payload, key)))


def _read_cache(hwid: str) -> dict | None:
    if not _CACHE_FILE.exists():
        return None
    try:
        key = _derive_key(hwid)
        raw = base64.b64decode(_CACHE_FILE.read_bytes())
        return json.loads(_xor(raw, key))
    except Exception:
        return None


def clear_cache() -> None:
    if _CACHE_FILE.exists():
        try:
            _CACHE_FILE.unlink()
        except OSError:
            pass


def _save_session(device_token: str, expires_at: str, hwid: str, extra_hours: int = 24) -> None:
    _write_cache({
        "device_token": device_token,
        "expires_at": expires_at,
        "cached_until": (datetime.now(timezone.utc) + timedelta(hours=extra_hours)).isoformat(),
    }, hwid)


def _verify(status: str, expires_at: str, device_token: str, sig: str) -> bool:
    payload = f"{status}|{expires_at}|{device_token}"
    expected = _hmac.new(_SIG_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, sig)


def _post(endpoint: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = _urllib_req.Request(
        f"{_API_BASE}/{endpoint}",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ClipFlow"},
        method="POST",
    )
    try:
        with _urllib_req.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as exc:
        try:
            resp_body = json.loads(exc.read())
        except Exception:
            resp_body = {"status": "error"}
        return exc.code, resp_body
    except (URLError, OSError, TimeoutError):
        return 0, {"status": "offline"}
    except Exception:
        return 0, {"status": "offline"}


def activate(key: str) -> tuple[LicenseStatus, str]:
    """
    Kích hoạt key lần đầu.
    Returns: ("ok", device_token) hoặc (error_status, "").
    """
    hwid = get_hwid()
    code, resp = _post("activate/", {"key": key.strip().upper(), "hwid": hwid})

    if code == 0:
        return "offline", ""

    status: str = resp.get("status", "error")
    if status == "ok":
        device_token = resp.get("device_token", "")
        expires_at = resp.get("expires_at", "")
        sig = resp.get("sig", "")
        if not _verify("ok", expires_at, device_token, sig):
            return "invalid_sig", ""
        _save_session(device_token, expires_at, hwid)
        return "ok", device_token

    return status, ""  # type: ignore[return-value]


def validate() -> LicenseStatus:
    """
    Kiểm tra trạng thái license hiện tại.
    Dùng cache nếu còn hạn, không thì gọi server.
    """
    hwid = get_hwid()
    data = _read_cache(hwid)

    if data is None:
        return "not_found"

    device_token: str = data.get("device_token", "")
    if not device_token:
        return "not_found"

    # Kiểm tra local expiry (subscription)
    expires_at_str: str = data.get("expires_at", "")
    if expires_at_str:
        try:
            exp = datetime.fromisoformat(expires_at_str)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= datetime.now(timezone.utc):
                clear_cache()
                return "expired"
        except ValueError:
            pass

    # Kiểm tra cache còn hạn 24h không
    cached_until_str: str = data.get("cached_until", "")
    if cached_until_str:
        try:
            cached_until = datetime.fromisoformat(cached_until_str)
            if cached_until.tzinfo is None:
                cached_until = cached_until.replace(tzinfo=timezone.utc)
            if cached_until > datetime.now(timezone.utc):
                return "ok"
        except ValueError:
            pass

    # Cache hết hạn — gọi server
    code, resp = _post("validate/", {"device_token": device_token, "hwid": hwid})

    if code == 0:
        # Server offline — grace period 1h, cho qua
        data["cached_until"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _write_cache(data, hwid)
        return "ok"

    status: str = resp.get("status", "error")
    if status == "ok":
        new_device_token: str = resp.get("device_token", device_token)
        new_expires_at: str = resp.get("expires_at", expires_at_str)
        sig: str = resp.get("sig", "")
        if not _verify("ok", new_expires_at, new_device_token, sig):
            return "invalid_sig"
        _save_session(new_device_token, new_expires_at, hwid)
        return "ok"

    if status in ("expired", "revoked", "device_mismatch"):
        clear_cache()

    return status  # type: ignore[return-value]
