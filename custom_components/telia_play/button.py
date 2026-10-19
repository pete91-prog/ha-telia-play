"""Buttons that start partner activation without waiting for email."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device_info
from .const import DOMAIN
from .coordinator import TeliaPlayCoordinator
from .models import CatalogItem


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TeliaPlayCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    def _entities() -> list[TeliaPlayActivateButton]:
        entities: list[TeliaPlayActivateButton] = []
        menu = coordinator.data
        if menu is None:
            return entities
        for item in menu.streaming_services:
            if item.id in known:
                continue
            known.add(item.id)
            entities.append(TeliaPlayActivateButton(coordinator, item.id))
        return entities

    async_add_entities(_entities())

    @callback
    def _add_new() -> None:
        additions = _entities()
        if additions:
            async_add_entities(additions)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class TeliaPlayActivateButton(CoordinatorEntity[TeliaPlayCoordinator], ButtonEntity):
    """Open Telia's activation URL for one streaming service."""

    _attr_has_entity_name = True
    _attr_translation_key = "activate"
    _attr_icon = "mdi:qrcode"

    def __init__(self, coordinator: TeliaPlayCoordinator, item_id: str) -> None:
        super().__init__(coordinator)
        self._item_id = item_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_activate_{item_id}"
        self._attr_device_info = device_info(coordinator.entry)

    @property
    def item(self) -> CatalogItem | None:
        if self.coordinator.data is None:
            return None
        for item in self.coordinator.data.streaming_services:
            if item.id == self._item_id:
                return item
        return None

    @property
    def available(self) -> bool:
        item = self.item
        if not super().available or item is None:
            return False
        result = self.coordinator.activation_for(self._item_id)
        return item.needs_activation or (result is not None and not result.done)

    @property
    def translation_placeholders(self) -> dict[str, str]:
        item = self.item
        return {"name": item.name if item else "service"}

    async def async_press(self) -> None:
        item = self.item
        if item is None:
            return
        await self.coordinator.async_activate_item(item, notify=True)
