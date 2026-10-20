"""Login tries the same email/password paths as the Telia Play website."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "telia_play"
_PACKAGE = "telia_play_login_pkg"
if _PACKAGE not in sys.modules:
    pkg = type(sys)("pkg")
    pkg.__path__ = [str(_ROOT)]
    pkg.__package__ = _PACKAGE
    sys.modules[_PACKAGE] = pkg


def _load(name: str, file_name: str):
    path = _ROOT / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load(f"{_PACKAGE}.const", "const.py")
_load(f"{_PACKAGE}.models", "models.py")
_load(f"{_PACKAGE}.activation", "activation.py")
api = _load(f"{_PACKAGE}.api", "api.py")

TeliaAuthError = api.TeliaAuthError
TeliaPlayClient = api.TeliaPlayClient


class _Resp:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self.content_type = "application/json"
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Session:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        status, payload = self.handler(method, url, kwargs)
        return _Resp(status, payload)


def _run(coro):
    return asyncio.run(coro)


class LoginTests(unittest.TestCase):
    def test_email_login_succeeds_on_country_gateway(self) -> None:
        def handler(method, url, kwargs):
            body = kwargs.get("json") or {}
            if url.endswith("/rest/v1/login"):
                self.assertEqual(body.get("username"), "user@gmail.com")
                self.assertEqual(body.get("password"), "browser-password")
                return 200, {
                    "accessToken": "tok",
                    "refreshToken": "ref",
                    "validTo": "2099-01-01T00:00:00Z",
                }
            raise AssertionError(url)

        client = TeliaPlayClient(
            _Session(handler),
            "NO",
            "User@Gmail.com",
            "browser-password",
            device_id="dev",
            boot_id="boot",
        )
        _run(client.async_login())
        self.assertEqual(client.access_token, "tok")
        self.assertEqual(client.username, "user@gmail.com")

    def test_falls_back_to_authenticate_oauth(self) -> None:
        def handler(method, url, kwargs):
            if url.endswith("/rest/v1/login"):
                return 401, {
                    "errorCode": "50002",
                    "errorMessage": "Username/password was incorrect",
                }
            if url.endswith("/rest/v1/authenticate"):
                body = kwargs.get("json") or {}
                self.assertEqual(body.get("username"), "user@example.com")
                return 200, {"redirectUri": "https://www.teliaplay.no/?code=GRANT"}
            if url.endswith("/rest/v1/oauth/token"):
                self.assertEqual((kwargs.get("params") or {}).get("code"), "GRANT")
                return 200, {
                    "accessToken": "oauth-tok",
                    "refreshToken": "oauth-ref",
                    "validTo": "2099-01-01T00:00:00Z",
                }
            raise AssertionError(url)

        client = TeliaPlayClient(
            _Session(handler),
            "NO",
            "user@example.com",
            "same-as-browser",
            device_id="dev",
            boot_id="boot",
        )
        _run(client.async_login())
        self.assertEqual(client.access_token, "oauth-tok")

    def test_all_password_paths_fail(self) -> None:
        def handler(method, url, kwargs):
            return 401, {
                "errorCode": "50002",
                "errorMessage": "Username/password was incorrect",
            }

        client = TeliaPlayClient(
            _Session(handler),
            "NO",
            "user@example.com",
            "same-as-browser",
            device_id="dev",
            boot_id="boot",
        )
        with self.assertRaises(TeliaAuthError):
            _run(client.async_login())


if __name__ == "__main__":
    unittest.main()
