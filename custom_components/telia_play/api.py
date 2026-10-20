"""Async client for Telia Play login and the Valgmeny (TV points) API."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession, ClientTimeout

from .activation import (
    code_payloads,
    extract_activation_code,
    infer_status,
    is_telia_host,
    with_partner_token,
    with_query,
)
from .const import (
    AUTH_PENDING_CODE,
    CLIENT_NAME,
    CLIENT_NAME_WEB,
    CLIENT_VERSION,
    COUNTRIES,
    ENGAGEMENT_GATEWAY,
    PURCHASE_GATEWAY,
    SHARED_LOGIN_URL,
    USER_AGENT,
    WHITE_LABEL,
)
from .models import (
    SelectionMenu,
    StreamingFamily,
    extract_oauth_code,
    families_from_api,
    menu_from_api,
    normalize_username,
    username_candidates,
)

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=30)


class TeliaPlayError(Exception):
    """Base error."""


class TeliaAuthError(TeliaPlayError):
    """Invalid credentials or expired session."""


class TeliaApiError(TeliaPlayError):
    """Unexpected API response."""


class TeliaPlayClient:
    """Talks to Telia Play account + purchase APIs."""

    def __init__(
        self,
        session: ClientSession,
        country: str,
        username: str,
        password: str,
        device_id: str | None = None,
        boot_id: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        valid_to: str | None = None,
    ) -> None:
        if country not in COUNTRIES:
            raise TeliaPlayError(f"Unsupported country: {country}")
        self._session = session
        self.country = country
        self._config = COUNTRIES[country]
        self._username_raw = (username or "").strip()
        self._username = normalize_username(self._username_raw)
        self._password = password
        self._client_name = CLIENT_NAME
        self.device_id = device_id or str(uuid.uuid4())
        self.boot_id = boot_id or str(uuid.uuid4())
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.valid_to = valid_to
        self._families: list[StreamingFamily] | None = None

    @property
    def username(self) -> str:
        return self._username

    def token_payload(self) -> dict[str, str]:
        return {
            "device_id": self.device_id,
            "boot_id": self.boot_id,
            "access_token": self.access_token or "",
            "refresh_token": self.refresh_token or "",
            "valid_to": self.valid_to or "",
        }

    def _headers(self, *, authorized: bool = False, login: bool = False) -> dict[str, str]:
        origin = self._config["login_origin"] if login else self._config["account_url"]
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": USER_AGENT,
            "x-country": self._config["code"],
            "tv-client-name": self._client_name,
            "tv-client-boot-id": self.boot_id,
            "tv-client-version": CLIENT_VERSION,
            "origin": origin,
            "referer": origin + "/",
        }
        if authorized and self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        authorized: bool = False,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        login: bool | None = None,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=self._headers(
                    authorized=authorized,
                    login=("/logingateway" in url) if login is None else login,
                ),
                timeout=_TIMEOUT,
            ) as response:
                if response.content_type and "json" in response.content_type:
                    payload = await response.json(content_type=None)
                else:
                    text = await response.text()
                    payload = {"raw": text, "status": response.status}
                if response.status in (401, 403):
                    raise TeliaAuthError(_error_message(payload) or f"HTTP {response.status}")
                if response.status >= 400:
                    raise TeliaApiError(_error_message(payload) or f"HTTP {response.status}")
                return payload
        except ClientError as err:
            raise TeliaApiError(str(err)) from err

    def _token_needs_refresh(self) -> bool:
        if not self.access_token:
            return True
        if not self.valid_to:
            return False
        expiry = _parse_valid_to(self.valid_to)
        if expiry is None:
            return False
        return datetime.now(timezone.utc) >= expiry - timedelta(minutes=5)

    def _store_tokens(self, payload: dict[str, Any]) -> None:
        if payload.get("errorMessage"):
            raise TeliaAuthError(str(payload["errorMessage"]))
        access = payload.get("accessToken")
        if not access:
            raise TeliaAuthError("Login did not return an access token")
        self.access_token = access
        if payload.get("refreshToken"):
            self.refresh_token = str(payload["refreshToken"])
        self.valid_to = str(payload.get("validTo") or "")

    async def async_login(self) -> None:
        """Sign in with the same email/mobile + password as teliaplay.no."""
        last_error: TeliaPlayError | None = None
        for username in username_candidates(self._username_raw or self._username):
            for client_name, kind, gateway in _login_attempts(self._config):
                self._client_name = client_name
                try:
                    if kind == "login":
                        await self._login_direct(gateway, username)
                    else:
                        await self._login_oauth(gateway, username)
                except TeliaAuthError as err:
                    last_error = err
                    _LOGGER.debug(
                        "Telia %s as %s via %s (%s) failed: %s",
                        kind,
                        username,
                        gateway,
                        client_name,
                        err,
                    )
                    continue
                self._username = username
                return
        raise last_error or TeliaAuthError("Username/password was incorrect")

    async def _login_direct(self, gateway: str, username: str) -> None:
        payload = await self._request(
            "POST",
            f"{gateway}/rest/v1/login",
            json_body={
                "deviceId": self.device_id,
                "username": username,
                "password": self._password,
                "deviceType": "WEB",
                "whiteLabelBrand": WHITE_LABEL,
            },
        )
        self._store_tokens(payload)

    async def _login_oauth(self, gateway: str, username: str) -> None:
        play_url = str(self._config.get("play_url") or self._config["account_url"])
        payload = await self._request(
            "POST",
            f"{gateway}/rest/v1/authenticate",
            params={"redirectUri": play_url + "/"},
            json_body={
                "deviceId": self.device_id,
                "deviceType": "WEB",
                "password": self._password,
                "username": username,
                "whiteLabelBrand": WHITE_LABEL,
            },
        )
        if isinstance(payload, dict) and payload.get("accessToken"):
            self._store_tokens(payload)
            return
        code = extract_oauth_code(payload if isinstance(payload, dict) else {})
        if not code:
            raise TeliaAuthError("Telia authenticate did not return an access code")
        tokens = await self._request(
            "POST",
            f"{gateway}/rest/v1/oauth/token",
            params={"code": code},
        )
        self._store_tokens(tokens)

    async def async_start_device_login(self) -> dict[str, Any]:
        """Start the official TV-code pairing flow (BankID / Google on the phone)."""
        url = (
            f"{self._config['login_url']}/rest/v1/device/authorization"
            f"?deviceId={self.device_id}&deviceType=WEB&whiteLabelBrand={WHITE_LABEL}"
        )
        payload = await self._request("GET", url)
        if not isinstance(payload, dict) or not payload.get("deviceCode"):
            raise TeliaApiError("Telia did not return a TV pairing code")
        return {
            "device_code": str(payload["deviceCode"]),
            "user_code": str(payload.get("userCode") or ""),
            "verification_uri": str(
                payload.get("verificationUriComplete")
                or payload.get("verificationUri")
                or self._config["login_origin"]
            ),
            "interval": int(payload.get("interval") or 5),
            "expires_in": int(payload.get("expiresIn") or 7200),
        }

    async def async_poll_device_login(self, device_code: str) -> dict[str, Any] | None:
        """Return tokens when the user has approved the TV code, else None if still waiting."""
        url = f"{self._config['login_url']}/rest/v1/device/token"
        headers = self._headers(login=True)
        headers["content-type"] = "application/x-www-form-urlencoded"
        try:
            async with self._session.request(
                "POST",
                url,
                data={"deviceCode": device_code},
                headers=headers,
                timeout=_TIMEOUT,
            ) as response:
                if response.content_type and "json" in response.content_type:
                    payload = await response.json(content_type=None)
                else:
                    text = await response.text()
                    payload = {"raw": text, "status": response.status}
                if response.status in (200, 201) and isinstance(payload, dict):
                    self._store_tokens(payload)
                    return payload
                if _is_auth_pending(payload, response.status):
                    return None
                if response.status in (401, 403):
                    raise TeliaAuthError(_error_message(payload) or f"HTTP {response.status}")
                raise TeliaApiError(_error_message(payload) or f"HTTP {response.status}")
        except ClientError as err:
            raise TeliaApiError(str(err)) from err

    async def async_account_username(self) -> str | None:
        await self.async_ensure_token()
        try:
            payload = await self._request(
                "GET",
                f"{ENGAGEMENT_GATEWAY}/rest/secure/v2/account/info",
                authorized=True,
            )
        except TeliaPlayError:
            return None
        if not isinstance(payload, dict):
            return None
        for key in ("displayUsername", "username", "email", "msisdn", "phoneNumber"):
            value = payload.get(key)
            if value:
                return str(value)
        return None

    async def async_refresh(self) -> None:
        if not self.refresh_token:
            await self.async_login()
            return
        url = f"{self._config['login_url']}/rest/v1/login/refresh"
        try:
            payload = await self._request(
                "POST",
                url,
                json_body={
                    "deviceId": self.device_id,
                    "deviceType": "WEB",
                    "refreshToken": self.refresh_token,
                },
            )
            self._store_tokens(payload)
        except (TeliaAuthError, TeliaApiError) as err:
            _LOGGER.info("Refresh token failed (%s), logging in again", err)
            await self.async_login()

    async def async_ensure_token(self) -> None:
        if self._token_needs_refresh():
            if self.refresh_token and self.access_token:
                await self.async_refresh()
            else:
                await self.async_login()

    async def async_selection_menu(self) -> SelectionMenu:
        await self.async_ensure_token()
        payload = await self._request(
            "GET",
            f"{PURCHASE_GATEWAY}/rest/secure/v1/selection-menu",
            authorized=True,
        )
        if not isinstance(payload, dict) or "selectables" not in payload:
            raise TeliaApiError("Selection menu response was not recognised")
        return menu_from_api(payload)

    async def async_save_selection(self, ids: list[str]) -> SelectionMenu:
        await self.async_ensure_token()
        await self._request(
            "POST",
            f"{PURCHASE_GATEWAY}/rest/secure/v1/selection-menu",
            authorized=True,
            json_body={"ids": ids},
        )
        return await self.async_selection_menu()

    async def async_streaming_families(self, *, force: bool = False) -> list[StreamingFamily]:
        if self._families is not None and not force:
            return self._families
        families_raw = await self._request(
            "GET",
            f"{PURCHASE_GATEWAY}/rest/v1/tv-streaming-service-families",
        )
        services_raw = await self._request(
            "GET",
            f"{PURCHASE_GATEWAY}/rest/v1/tv-streaming-services",
        )
        self._families = families_from_api(
            families_raw if isinstance(families_raw, list) else [],
            services_raw if isinstance(services_raw, list) else [],
        )
        return self._families

    async def async_value_added_services(self) -> Any:
        await self.async_ensure_token()
        try:
            return await self._request(
                "GET",
                f"{ENGAGEMENT_GATEWAY}/rest/secure/v2/valueaddedservice/services",
                authorized=True,
            )
        except TeliaPlayError as err:
            _LOGGER.debug("Value-added services list unavailable: %s", err)
            return None

    async def async_partner_token(self, partner_id: str) -> str | None:
        """SSO token Telia issues for a streaming partner — no email wait."""
        if not partner_id:
            return None
        await self.async_ensure_token()
        url = (
            f"{self._config['login_url']}/rest/secure/v1/partner/"
            f"{partner_id}/login"
        )
        try:
            payload = await self._request(
                "POST",
                url,
                authorized=True,
                json_body={},
            )
        except TeliaPlayError as err:
            _LOGGER.debug("Partner token for %s failed: %s", partner_id, err)
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("partnerToken") or payload.get("token")
        return str(token) if token else None

    async def async_open_activation_url(
        self,
        url: str,
        *,
        partner_token: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        """Open the same activation link Telia puts in QR / email, using this session."""
        if not url:
            raise TeliaApiError("No activation URL")
        if partner_token and is_telia_host(url):
            target = with_partner_token(url, partner_token)
        elif partner_token:
            target = with_query(url, partnerToken=partner_token)
        else:
            target = url
        if code:
            target = with_query(target, code=code, activationCode=code)
        result = await self._fetch_url(target, partner_token=partner_token)
        if code and is_telia_host(url) and result.get("status", 0) >= 400:
            posted = await self._post_code(url, code, partner_token=partner_token)
            if posted is not None:
                result = posted
        body = str(result.get("text") or "")
        pairing = extract_activation_code(body)
        status = infer_status(
            final_url=result.get("url"),
            body=body,
            http_status=result.get("status"),
        )
        json_payload = result.get("json")
        if isinstance(json_payload, dict):
            nested = (
                json_payload.get("activationUrl")
                or json_payload.get("url")
                or json_payload.get("redirectUrl")
            )
            if nested and not pairing:
                follow = await self._fetch_url(str(nested), partner_token=partner_token)
                body = str(follow.get("text") or body)
                pairing = extract_activation_code(body) or pairing
                result = follow
                status = infer_status(
                    final_url=result.get("url"),
                    body=body,
                    http_status=result.get("status"),
                )
        if pairing and status == "started":
            status = "code_ready"
        return {
            "status": status,
            "http_status": result.get("status"),
            "url": result.get("url") or target,
            "requested_url": target,
            "pairing_code": pairing,
            "json": json_payload,
        }

    async def _post_code(
        self, url: str, code: str, *, partner_token: str | None
    ) -> dict[str, Any] | None:
        last: dict[str, Any] | None = None
        for body in code_payloads(code):
            try:
                last = await self._fetch_url(
                    url,
                    method="POST",
                    json_body=body,
                    partner_token=partner_token,
                )
            except TeliaPlayError:
                continue
            status = last.get("status") or 0
            if 200 <= status < 300:
                return last
        return last

    async def _fetch_url(
        self,
        url: str,
        *,
        method: str = "GET",
        json_body: dict[str, Any] | None = None,
        partner_token: str | None = None,
    ) -> dict[str, Any]:
        current = url
        last: dict[str, Any] | None = None
        request_method = method
        try:
            for _ in range(8):
                authorized = is_telia_host(current)
                if authorized:
                    await self.async_ensure_token()
                    headers = self._headers(authorized=True)
                    if partner_token:
                        headers["x-partner-token"] = partner_token
                else:
                    headers = {"accept": "*/*", "user-agent": USER_AGENT}
                async with self._session.request(
                    request_method,
                    current,
                    json=json_body if request_method != "GET" else None,
                    headers=headers,
                    timeout=_TIMEOUT,
                    allow_redirects=False,
                ) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            text = await response.text()
                            last = {
                                "status": response.status,
                                "url": current,
                                "text": text,
                                "json": None,
                            }
                            break
                        if response.status == 303:
                            request_method = "GET"
                            json_body = None
                        current = urljoin(current, location)
                        continue
                    text = await response.text()
                    payload: Any = None
                    content_type = response.content_type or ""
                    if "json" in content_type or (text and text.lstrip()[:1] in "{["):
                        try:
                            payload = json.loads(text)
                        except ValueError:
                            payload = None
                    last = {
                        "status": response.status,
                        "url": str(response.url) or current,
                        "text": text,
                        "json": payload,
                    }
                    break
        except ClientError as err:
            raise TeliaApiError(str(err)) from err
        if last is None:
            raise TeliaApiError(f"No response from {url}")
        return last


def _login_attempts(config: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Client name, login kind, and gateway to try — same paths as the website / Kodi."""
    country = str(config["login_url"])
    seen: set[tuple[str, str, str]] = set()
    attempts: list[tuple[str, str, str]] = []
    for client_name, kind, gateway in (
        (CLIENT_NAME, "login", country),
        (CLIENT_NAME_WEB, "login", country),
        (CLIENT_NAME_WEB, "oauth", SHARED_LOGIN_URL),
        (CLIENT_NAME, "oauth", country),
        (CLIENT_NAME, "login", SHARED_LOGIN_URL),
    ):
        item = (client_name, kind, gateway)
        if item in seen:
            continue
        seen.add(item)
        attempts.append(item)
    return attempts


def _is_auth_pending(payload: Any, status: int) -> bool:
    if not isinstance(payload, dict):
        return status in (400, 428)
    code = str(payload.get("errorCode") or "")
    message = str(payload.get("errorMessage") or payload.get("message") or "").lower()
    return code == AUTH_PENDING_CODE or "pending" in message or "authorization pending" in message


def _error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return str(payload) if payload else None
    for key in ("errorMessage", "message", "error"):
        if payload.get(key):
            return str(payload[key])
    if payload.get("errorCode"):
        return f"Error {payload['errorCode']}"
    return None


def _parse_valid_to(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
