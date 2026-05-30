from __future__ import annotations

import hashlib
import hmac as _hmac_mod
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app.license_manager as lm


def _sign(status: str, expires_at: str, device_token: str) -> str:
    payload = f"{status}|{expires_at}|{device_token}"
    return _hmac_mod.new(lm._SIG_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso() -> str:
    return "2000-01-01T00:00:00+00:00"


class GetHwidTest(unittest.TestCase):
    def test_returns_32_char_hex_string(self) -> None:
        hwid = lm.get_hwid()
        self.assertEqual(len(hwid), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in hwid))

    def test_is_deterministic_across_calls(self) -> None:
        self.assertEqual(lm.get_hwid(), lm.get_hwid())


class XorTest(unittest.TestCase):
    def test_round_trip_restores_original_bytes(self) -> None:
        data = b"secret payload data"
        key = b"some key"
        self.assertEqual(lm._xor(lm._xor(data, key), key), data)

    def test_wraps_key_for_data_longer_than_key(self) -> None:
        data = b"A" * 100
        key = b"short"
        result = lm._xor(data, key)
        self.assertEqual(len(result), 100)
        self.assertEqual(lm._xor(result, key), data)


class CacheRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = lm._CACHE_DIR
        self._orig_file = lm._CACHE_FILE
        lm._CACHE_DIR = Path(self._tmp.name)
        lm._CACHE_FILE = lm._CACHE_DIR / "session.dat"

    def tearDown(self) -> None:
        lm._CACHE_DIR = self._orig_dir
        lm._CACHE_FILE = self._orig_file
        self._tmp.cleanup()

    def test_write_then_read_returns_identical_data(self) -> None:
        hwid = "a" * 32
        data = {"device_token": "tok123", "expires_at": _future_iso(), "cached_until": _future_iso(1)}
        lm._write_cache(data, hwid)
        self.assertEqual(lm._read_cache(hwid), data)

    def test_read_with_wrong_hwid_does_not_return_original_data(self) -> None:
        hwid_a = "a" * 32
        hwid_b = "b" * 32
        lm._write_cache({"device_token": "secret"}, hwid_a)
        result = lm._read_cache(hwid_b)
        if result is not None:
            self.assertNotEqual(result.get("device_token"), "secret")

    def test_read_returns_none_when_no_file_exists(self) -> None:
        self.assertIsNone(lm._read_cache("a" * 32))

    def test_read_returns_none_for_corrupted_file(self) -> None:
        lm._CACHE_FILE.write_bytes(b"not base64 or valid data!!!@#$")
        self.assertIsNone(lm._read_cache("a" * 32))

    def test_clear_cache_deletes_the_file(self) -> None:
        hwid = "a" * 32
        lm._write_cache({"device_token": "tok"}, hwid)
        self.assertTrue(lm._CACHE_FILE.exists())
        lm.clear_cache()
        self.assertFalse(lm._CACHE_FILE.exists())

    def test_clear_cache_is_safe_when_no_file_exists(self) -> None:
        lm.clear_cache()


class VerifySignatureTest(unittest.TestCase):
    def test_accepts_valid_hmac_signature(self) -> None:
        expires = _future_iso()
        token = "tok123"
        sig = _sign("ok", expires, token)
        self.assertTrue(lm._verify("ok", expires, token, sig))

    def test_rejects_tampered_status(self) -> None:
        expires = _future_iso()
        sig = _sign("ok", expires, "tok")
        self.assertFalse(lm._verify("expired", expires, "tok", sig))

    def test_rejects_tampered_device_token(self) -> None:
        expires = _future_iso()
        sig = _sign("ok", expires, "tok123")
        self.assertFalse(lm._verify("ok", expires, "tok999", sig))

    def test_rejects_tampered_expires_at(self) -> None:
        expires = _future_iso()
        sig = _sign("ok", expires, "tok")
        self.assertFalse(lm._verify("ok", "2099-12-31T00:00:00+00:00", "tok", sig))

    def test_rejects_garbage_signature(self) -> None:
        self.assertFalse(lm._verify("ok", _future_iso(), "tok", "badhex"))


class ValidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = lm._CACHE_DIR
        self._orig_file = lm._CACHE_FILE
        lm._CACHE_DIR = Path(self._tmp.name)
        lm._CACHE_FILE = lm._CACHE_DIR / "session.dat"
        self._hwid = lm.get_hwid()

    def tearDown(self) -> None:
        lm._CACHE_DIR = self._orig_dir
        lm._CACHE_FILE = self._orig_file
        self._tmp.cleanup()

    def _write(self, overrides: dict) -> None:
        data = {
            "device_token": "tok123",
            "expires_at": _future_iso(30),
            "cached_until": _future_iso(1),
        }
        data.update(overrides)
        lm._write_cache(data, self._hwid)

    def test_returns_not_found_when_no_cache_exists(self) -> None:
        self.assertEqual(lm.validate(), "not_found")

    def test_returns_not_found_when_cache_has_no_device_token(self) -> None:
        self._write({"device_token": ""})
        self.assertEqual(lm.validate(), "not_found")

    def test_returns_ok_when_cache_is_fresh(self) -> None:
        self._write({})
        self.assertEqual(lm.validate(), "ok")

    def test_returns_expired_and_clears_cache_when_subscription_expired(self) -> None:
        self._write({"expires_at": _past_iso()})
        self.assertEqual(lm.validate(), "expired")
        self.assertFalse(lm._CACHE_FILE.exists())

    def test_calls_server_and_returns_ok_when_cache_window_expired(self) -> None:
        expires = _future_iso(30)
        token = "tok123"
        sig = _sign("ok", expires, token)
        server_resp = {"status": "ok", "device_token": token, "expires_at": expires, "sig": sig}
        self._write({"cached_until": _past_iso()})

        with patch("app.license_manager._post", return_value=(200, server_resp)):
            self.assertEqual(lm.validate(), "ok")

    def test_grants_grace_period_when_server_is_offline(self) -> None:
        self._write({"cached_until": _past_iso()})
        with patch("app.license_manager._post", return_value=(0, {"status": "offline"})):
            self.assertEqual(lm.validate(), "ok")

    def test_clears_cache_when_server_returns_revoked(self) -> None:
        self._write({"cached_until": _past_iso()})
        with patch("app.license_manager._post", return_value=(200, {"status": "revoked"})):
            result = lm.validate()
        self.assertEqual(result, "revoked")
        self.assertFalse(lm._CACHE_FILE.exists())

    def test_clears_cache_when_server_returns_device_mismatch(self) -> None:
        self._write({"cached_until": _past_iso()})
        with patch("app.license_manager._post", return_value=(200, {"status": "device_mismatch"})):
            lm.validate()
        self.assertFalse(lm._CACHE_FILE.exists())

    def test_returns_invalid_sig_when_server_response_is_tampered(self) -> None:
        self._write({"cached_until": _past_iso()})
        tampered = {"status": "ok", "device_token": "tok123", "expires_at": _future_iso(), "sig": "badsig"}
        with patch("app.license_manager._post", return_value=(200, tampered)):
            self.assertEqual(lm.validate(), "invalid_sig")


class ActivateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = lm._CACHE_DIR
        self._orig_file = lm._CACHE_FILE
        lm._CACHE_DIR = Path(self._tmp.name)
        lm._CACHE_FILE = lm._CACHE_DIR / "session.dat"

    def tearDown(self) -> None:
        lm._CACHE_DIR = self._orig_dir
        lm._CACHE_FILE = self._orig_file
        self._tmp.cleanup()

    def test_saves_session_and_returns_ok_on_success(self) -> None:
        expires = _future_iso(30)
        token = "device_tok_abc"
        sig = _sign("ok", expires, token)
        resp = {"status": "ok", "device_token": token, "expires_at": expires, "sig": sig}

        with patch("app.license_manager._post", return_value=(200, resp)):
            status, returned = lm.activate("CF-TEST-TEST-TEST")

        self.assertEqual(status, "ok")
        self.assertTrue(lm._CACHE_FILE.exists())

    def test_returns_offline_when_server_is_unreachable(self) -> None:
        with patch("app.license_manager._post", return_value=(0, {"status": "offline"})):
            status, token = lm.activate("CF-TEST-TEST-TEST")
        self.assertEqual(status, "offline")
        self.assertEqual(token, "")

    def test_returns_not_found_when_key_does_not_exist(self) -> None:
        with patch("app.license_manager._post", return_value=(404, {"status": "not_found"})):
            status, token = lm.activate("CF-FAKE-FAKE-FAKE")
        self.assertEqual(status, "not_found")
        self.assertEqual(token, "")

    def test_returns_invalid_sig_when_server_response_is_untrustworthy(self) -> None:
        resp = {"status": "ok", "device_token": "tok", "expires_at": _future_iso(), "sig": "badsig"}
        with patch("app.license_manager._post", return_value=(200, resp)):
            status, _ = lm.activate("CF-TEST-TEST-TEST")
        self.assertEqual(status, "invalid_sig")
        self.assertFalse(lm._CACHE_FILE.exists())

    def test_does_not_save_cache_after_rejected_activation(self) -> None:
        with patch("app.license_manager._post", return_value=(400, {"status": "revoked"})):
            lm.activate("CF-TEST-TEST-TEST")
        self.assertFalse(lm._CACHE_FILE.exists())


if __name__ == "__main__":
    unittest.main()
