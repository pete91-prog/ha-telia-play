"""Diagnostics for Telia Play."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ACCESS_TOKEN, CONF_PASSWORD, CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import TeliaPlayCoordinator

TO_REDACT = {CONF_PASSWORD, CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, "raw"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: TeliaPlayCoordinator = hass.data[DOMAIN][entry.entry_id]
    menu = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "points": None
        if menu is None
        else {
            "total": menu.points_total,
            "used": menu.points_used,
            "remaining": menu.points_remaining,
            "streaming": [
                {
                    "id": item.id,
                    "name": item.name,
                    "points": item.points,
                    "enabled": item.is_selected,
                    "needs_activation": item.needs_activation,
                    "family": item.family_name,
                    "activation_status": None
                    if coordinator.activation_for(item.id) is None
                    else coordinator.activation_for(item.id).status,
                }
                for item in menu.streaming_services
            ],
        },
    }
