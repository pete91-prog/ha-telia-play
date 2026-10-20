"""Paste a Max SMS link or email activation code."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
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
    async_add_entities([TeliaPlayActivationText(coordinator)])


class TeliaPlayActivationText(CoordinatorEntity[TeliaPlayCoordinator], TextEntity):
    """Single box for the Max SMS link, then the email one-time code."""

    _attr_has_entity_name = True
    _attr_translation_key = "activation_code"
    _attr_native_min = 0
    _attr_native_max = 2048
    _attr_mode = TextMode.TEXT
    _attr_icon = "mdi:message-text"

    def __init__(self, coordinator: TeliaPlayCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_activation_code"
        self._attr_device_info = device_info(coordinator.entry)
        self._attr_native_value = ""

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        if any(item.needs_activation for item in self.coordinator.data.streaming_services):
            return True
        return any(
            result is not None and not result.done
            for result in self.coordinator.activations.values()
        )

    async def async_set_value(self, value: str) -> None:
        pasted = (value or "").strip()
        self._attr_native_value = pasted
        self.async_write_ha_state()
        if not pasted:
            return
        result = await self.coordinator.async_submit_code(None, pasted)
        if result.done:
            self._attr_native_value = ""
        self.async_write_ha_state()
