"""Sensors for Telia Play TV points."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device_info
from .const import DOMAIN
from .coordinator import TeliaPlayCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TeliaPlayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            TeliaPlayPointsSensor(coordinator, "remaining"),
            TeliaPlayPointsSensor(coordinator, "used"),
            TeliaPlayPointsSensor(coordinator, "total"),
        ]
    )


class TeliaPlayPointsSensor(CoordinatorEntity[TeliaPlayCoordinator], SensorEntity):
    """Remaining / used / total TV points."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "points"
    _attr_icon = "mdi:star-four-points"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TeliaPlayCoordinator, kind: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_unique_id = f"{coordinator.entry.entry_id}_points_{kind}"
        self._attr_translation_key = f"points_{kind}"
        self._attr_device_info = device_info(coordinator.entry)

    @property
    def native_value(self) -> int | None:
        menu = self.coordinator.data
        if menu is None:
            return None
        if self._kind == "remaining":
            return menu.points_remaining
        if self._kind == "used":
            return menu.points_used
        return menu.points_total

    @property
    def extra_state_attributes(self) -> dict | None:
        menu = self.coordinator.data
        if menu is None or self._kind != "remaining":
            return None
        return {
            "used": menu.points_used,
            "total": menu.points_total,
            "enabled": [item.name for item in menu.selected],
            "enabled_ids": menu.selected_ids,
            "streaming_services": [
                {
                    "name": item.name,
                    "points": item.points,
                    "enabled": item.is_selected,
                    "needs_activation": item.needs_activation,
                    "activation_url": item.activation_url,
                    "family": item.family_name,
                }
                for item in menu.streaming_services
            ],
        }
