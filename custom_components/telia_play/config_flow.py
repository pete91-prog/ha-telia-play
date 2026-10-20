"""Config flow for Telia Play Valgmeny."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TeliaApiError, TeliaAuthError, TeliaPlayClient, TeliaPlayError
from .const import (
    CONF_BOOT_ID,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_INCLUDE_CHANNELS,
    CONF_PASSWORD,
    CONF_USERNAME,
    COUNTRIES,
    DEFAULT_COUNTRY,
    DOMAIN,
)
from .models import normalize_username

COUNTRY_OPTIONS = [
    selector.SelectOptionDict(value=code, label=info["name"])
    for code, info in COUNTRIES.items()
]


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_COUNTRY, default=defaults.get(CONF_COUNTRY, DEFAULT_COUNTRY)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=COUNTRY_OPTIONS, mode="dropdown")
            ),
            vol.Optional(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(autocomplete="username")
            ),
            vol.Optional(CONF_PASSWORD, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_INCLUDE_CHANNELS,
                default=defaults.get(CONF_INCLUDE_CHANNELS, False),
            ): selector.BooleanSelector(),
        }
    )


class TeliaPlayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Telia Play with the same email/password as the website."""

    VERSION = 1

    def __init__(self) -> None:
        self._country = DEFAULT_COUNTRY
        self._include_channels = False
        self._username = ""
        self._password = ""
        self._device_id = str(uuid.uuid4())
        self._boot_id = str(uuid.uuid4())
        self._pairing: dict[str, Any] | None = None
        self._task: asyncio.Task[dict[str, str]] | None = None
        self._tokens: dict[str, str] | None = None
        self._error: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"error": self._error or ""}
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_user_schema(),
                description_placeholders=placeholders,
            )
        self._country = user_input[CONF_COUNTRY]
        self._include_channels = bool(user_input.get(CONF_INCLUDE_CHANNELS))
        username = str(user_input.get(CONF_USERNAME) or "").strip()
        password = str(user_input.get(CONF_PASSWORD) or "")
        self._username = normalize_username(username)
        self._password = password
        if not username or not password:
            return await self.async_step_tv_code()
        try:
            tokens = await self._async_validate_password(username, password)
        except TeliaAuthError as err:
            self._error = str(err)
            return await self.async_step_password_fallback()
        except TeliaApiError:
            errors["base"] = "no_selection_menu"
        except TeliaPlayError:
            errors["base"] = "cannot_connect"
        else:
            return await self._async_finish(tokens)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(
                {
                    CONF_COUNTRY: self._country,
                    CONF_USERNAME: username,
                    CONF_INCLUDE_CHANNELS: self._include_channels,
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_password_fallback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Password was rejected — continue on the same account with a TV code."""
        if user_input is None:
            return self.async_show_form(
                step_id="password_fallback",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "username": self._username or "",
                    "error": self._error or "",
                },
            )
        return await self.async_step_tv_code()

    async def async_step_tv_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._task is None:
            try:
                await self._async_start_pairing()
            except TeliaPlayError:
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(
                        {
                            CONF_COUNTRY: self._country,
                            CONF_USERNAME: self._username,
                            CONF_INCLUDE_CHANNELS: self._include_channels,
                        }
                    ),
                    errors={"base": "cannot_connect"},
                )
            self._task = self.hass.async_create_task(self._async_wait_pairing())

        if not self._task.done():
            pairing = self._pairing or {}
            return self.async_show_progress(
                step_id="tv_code",
                progress_action="wait_approve",
                progress_task=self._task,
                description_placeholders={
                    "code": pairing.get("user_code", ""),
                    "url": pairing.get("verification_uri", "https://login.teliaplay.no"),
                },
            )

        try:
            tokens = await self._task
        except TeliaAuthError as err:
            self._error = str(err)
            return self.async_show_progress_done(next_step_id="tv_failed")
        except TeliaApiError:
            self._error = "no_selection_menu"
            return self.async_show_progress_done(next_step_id="tv_failed")
        except TeliaPlayError:
            self._error = "cannot_connect"
            return self.async_show_progress_done(next_step_id="tv_failed")
        self._tokens = tokens
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._tokens:
            return await self.async_step_user()
        return await self._async_finish(self._tokens)

    async def async_step_tv_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._task = None
        self._pairing = None
        error = self._error or "invalid_auth"
        if error not in {"invalid_auth", "cannot_connect", "no_selection_menu"}:
            error = "tv_code_expired"
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(
                {
                    CONF_COUNTRY: self._country,
                    CONF_USERNAME: self._username,
                    CONF_INCLUDE_CHANNELS: self._include_channels,
                }
            ),
            errors={"base": error},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            username = str(
                user_input.get(CONF_USERNAME, entry.data[CONF_USERNAME]) or ""
            ).strip()
            try:
                tokens = await self._async_validate_password(
                    username,
                    user_input[CONF_PASSWORD],
                    device_id=entry.data.get(CONF_DEVICE_ID),
                    boot_id=entry.data.get(CONF_BOOT_ID),
                )
            except TeliaAuthError as err:
                errors["base"] = "invalid_auth"
                placeholders["error"] = str(err)
            except TeliaPlayError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: self._username or normalize_username(username),
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        **tokens,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
                ): selector.TextSelector(),
                vol.Required(CONF_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_start_pairing(self) -> None:
        client = self._client()
        self._pairing = await client.async_start_device_login()
        self._device_id = client.device_id
        self._boot_id = client.boot_id

    async def _async_wait_pairing(self) -> dict[str, str]:
        if not self._pairing:
            raise TeliaPlayError("Pairing was not started")
        client = self._client()
        interval = max(2, int(self._pairing.get("interval") or 5))
        remaining = min(int(self._pairing.get("expires_in") or 7200), 600)
        elapsed = 0
        while elapsed < remaining:
            await asyncio.sleep(interval)
            elapsed += interval
            tokens = await client.async_poll_device_login(self._pairing["device_code"])
            if tokens is None:
                continue
            await client.async_selection_menu()
            username = await client.async_account_username()
            if username:
                self._username = normalize_username(username)
            else:
                self._username = f"tv-{client.device_id[:8]}"
            return client.token_payload()
        raise TeliaAuthError("The TV code expired before it was approved")

    def _client(
        self,
        username: str = "",
        password: str = "",
        device_id: str | None = None,
        boot_id: str | None = None,
    ) -> TeliaPlayClient:
        return TeliaPlayClient(
            session=async_get_clientsession(self.hass),
            country=self._country or DEFAULT_COUNTRY,
            username=username,
            password=password,
            device_id=device_id or self._device_id,
            boot_id=boot_id or self._boot_id,
        )

    async def _async_validate_password(
        self,
        username: str,
        password: str,
        device_id: str | None = None,
        boot_id: str | None = None,
    ) -> dict[str, str]:
        client = self._client(username, password, device_id, boot_id)
        await client.async_login()
        await client.async_selection_menu()
        account = await client.async_account_username()
        self._username = normalize_username(account or client.username or username)
        self._device_id = client.device_id
        self._boot_id = client.boot_id
        return client.token_payload()

    async def _async_finish(self, tokens: dict[str, str]) -> ConfigFlowResult:
        unique = f"{self._country}:{self._username.lower() or tokens.get('device_id')}"
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()
        title = f"Telia Play ({COUNTRIES[self._country]['name']})"
        return self.async_create_entry(
            title=title,
            data={
                CONF_COUNTRY: self._country,
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                **tokens,
            },
            options={CONF_INCLUDE_CHANNELS: self._include_channels},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TeliaPlayOptionsFlow()


class TeliaPlayOptionsFlow(OptionsFlow):
    """Toggle whether channel packages are exposed as switches."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_INCLUDE_CHANNELS,
                    default=self.config_entry.options.get(CONF_INCLUDE_CHANNELS, False),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
