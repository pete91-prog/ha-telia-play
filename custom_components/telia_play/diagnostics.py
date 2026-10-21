"""Diagnostics for Telia Play — no personal data leaves Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_COUNTRY, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import TeliaPlayCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: TeliaPlayCoordinator = hass.data[DOMAIN][entry.entry_id]
    menu = coordinator.data
    return {
        "country": entry.data.get(CONF_COUNTRY),
        "has_telia_login": bool(entry.data.get(CONF_USERNAME)),
        "has_telia_password": bool(entry.data.get(CONF_PASSWORD)),
        "imap_configured": coordinator.imap_configured,
        "points": None
        if menu is None
        else {
            "total": menu.points_total,
            "used": menu.points_used,
            "remaining": menu.points_remaining,
            "streaming": [
                {
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
