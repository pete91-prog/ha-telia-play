"""Config flow for Telia Play Valgmeny."""

from __future__ import annotations

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
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(autocomplete="username")
            ),
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_INCLUDE_CHANNELS,
                default=defaults.get(CONF_INCLUDE_CHANNELS, False),
            ): selector.BooleanSelector(),
        }
    )


class TeliaPlayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Telia Play with username/password."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                tokens = await self._async_validate(user_input)
            except TeliaAuthError:
                errors["base"] = "invalid_auth"
            except TeliaApiError:
                errors["base"] = "no_selection_menu"
            except TeliaPlayError:
                errors["base"] = "cannot_connect"
            else:
                unique = f"{user_input[CONF_COUNTRY]}:{user_input[CONF_USERNAME].lower()}"
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()
                title = f"Telia Play ({COUNTRIES[user_input[CONF_COUNTRY]]['name']})"
                data = {
                    CONF_COUNTRY: user_input[CONF_COUNTRY],
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    **tokens,
                }
                options = {
                    CONF_INCLUDE_CHANNELS: user_input.get(CONF_INCLUDE_CHANNELS, False)
                }
                return self.async_create_entry(title=title, data=data, options=options)

        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input), errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            merged = {
                CONF_COUNTRY: entry.data[CONF_COUNTRY],
                CONF_USERNAME: user_input.get(CONF_USERNAME, entry.data[CONF_USERNAME]),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_DEVICE_ID: entry.data.get(CONF_DEVICE_ID),
                CONF_BOOT_ID: entry.data.get(CONF_BOOT_ID),
            }
            try:
                tokens = await self._async_validate(merged)
            except TeliaAuthError:
                errors["base"] = "invalid_auth"
            except TeliaPlayError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: merged[CONF_USERNAME],
                        CONF_PASSWORD: merged[CONF_PASSWORD],
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
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def _async_validate(self, user_input: Mapping[str, Any]) -> dict[str, str]:
        client = TeliaPlayClient(
            session=async_get_clientsession(self.hass),
            country=user_input[CONF_COUNTRY],
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
            device_id=user_input.get(CONF_DEVICE_ID) or str(uuid.uuid4()),
            boot_id=user_input.get(CONF_BOOT_ID) or str(uuid.uuid4()),
        )
        await client.async_login()
        await client.async_selection_menu()
        return client.token_payload()

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
