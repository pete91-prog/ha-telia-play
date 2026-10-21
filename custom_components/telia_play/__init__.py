"""Telia Play integration — enable streaming services with included TV points."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    ATTR_CODE,
    ATTR_SERVICE,
    ATTR_SERVICES,
    DOMAIN,
    PLATFORMS,
    SERVICE_ACTIVATE,
    SERVICE_DISABLE,
    SERVICE_ENABLE,
    SERVICE_SET_SERVICES,
    SERVICE_SUBMIT_CODE,
)
from .coordinator import TeliaPlayCoordinator

_LOGGER = logging.getLogger(__name__)


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Shared device for points sensor and service switches."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Telia",
        model="Telia Play Valgmeny",
        configuration_url="https://account.teliaplay.no/subscription/selection-menu",
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Telia Play from a config entry."""
    coordinator = TeliaPlayCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    coordinator.async_start_mail()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _coordinator(hass: HomeAssistant) -> TeliaPlayCoordinator:
    coordinators: dict[str, TeliaPlayCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise vol.Invalid("Telia Play is not configured")
    return next(iter(coordinators.values()))


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_ENABLE):
        return

    async def _enable(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        menu = await coordinator.async_set_named(call.data[ATTR_SERVICE], True)
        return _snapshot(coordinator, menu)

    async def _disable(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        menu = await coordinator.async_set_named(call.data[ATTR_SERVICE], False)
        return _snapshot(coordinator, menu)

    async def _set_services(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        menu = await coordinator.async_set_streaming_services(call.data[ATTR_SERVICES])
        return _snapshot(coordinator, menu)

    async def _activate(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        result = await coordinator.async_activate_named(call.data[ATTR_SERVICE])
        return result.as_dict()

    async def _submit_code(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        result = await coordinator.async_submit_code(
            call.data.get(ATTR_SERVICE), call.data[ATTR_CODE]
        )
        return result.as_dict()

    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE,
        _enable,
        schema=vol.Schema({vol.Required(ATTR_SERVICE): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE,
        _disable,
        schema=vol.Schema({vol.Required(ATTR_SERVICE): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SERVICES,
        _set_services,
        schema=vol.Schema({vol.Required(ATTR_SERVICES): vol.All(cv.ensure_list, [cv.string])}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ACTIVATE,
        _activate,
        schema=vol.Schema({vol.Required(ATTR_SERVICE): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SUBMIT_CODE,
        _submit_code,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_SERVICE): cv.string,
                vol.Required(ATTR_CODE): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


def _snapshot(coordinator: TeliaPlayCoordinator, menu) -> dict[str, Any]:
    return {
        "points_total": menu.points_total,
        "points_used": menu.points_used,
        "points_remaining": menu.points_remaining,
        "enabled": [item.name for item in menu.selected],
        "streaming": [
            {
                "id": item.id,
                "name": item.name,
                "points": item.points,
                "enabled": item.is_selected,
                "needs_activation": item.needs_activation,
                "activation_status": None
                if coordinator.activation_for(item.id) is None
                else coordinator.activation_for(item.id).status,
            }
            for item in menu.streaming_services
        ],
    }
