"""Switches that enable Telia Play streaming services with TV points."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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

    def _entities() -> list[TeliaPlayServiceSwitch]:
        entities: list[TeliaPlayServiceSwitch] = []
        for item in coordinator.items_for_switches():
            if item.id in known:
                continue
            known.add(item.id)
            entities.append(TeliaPlayServiceSwitch(coordinator, item.id))
        return entities

    async_add_entities(_entities())

    @callback
    def _add_new() -> None:
        additions = _entities()
        if additions:
            async_add_entities(additions)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class TeliaPlayServiceSwitch(CoordinatorEntity[TeliaPlayCoordinator], SwitchEntity):
    """Turn one Valgmeny item on or off."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset(
        {
            "activation_url",
            "partner_url",
            "pairing_code",
            "activation_message",
        }
    )

    def __init__(self, coordinator: TeliaPlayCoordinator, item_id: str) -> None:
        super().__init__(coordinator)
        self._item_id = item_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_item_{item_id}"
        self._attr_device_info = device_info(coordinator.entry)

    @property
    def item(self) -> CatalogItem | None:
        if self.coordinator.data is None:
            return None
        for item in self.coordinator.data.selectables:
            if item.id == self._item_id:
                return item
        return None

    @property
    def available(self) -> bool:
        return super().available and self.item is not None

    @property
    def name(self) -> str | None:
        item = self.item
        return item.name if item else None

    @property
    def icon(self) -> str:
        item = self.item
        if item and item.vod:
            return "mdi:play-box-multiple"
        return "mdi:television"

    @property
    def is_on(self) -> bool:
        item = self.item
        return bool(item and item.is_selected)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self.item
        menu = self.coordinator.data
        if item is None or menu is None:
            return {}
        remaining_if_enabled = menu.points_remaining if item.is_selected else (
            menu.points_remaining - item.points
        )
        result = self.coordinator.activation_for(item.id)
        return {
            "points": item.points,
            "kind": item.kind,
            "description": item.description,
            "needs_activation": item.needs_activation,
            "activation_url": item.activation_url,
            "activation_status": None if result is None else result.status,
            "activation_message": None if result is None else result.message,
            "pairing_code": None if result is None else result.pairing_code,
            "partner_url": None if result is None else result.partner_url,
            "family": item.family_name,
            "vas_hub_provisioning": item.vas_hub_provisioning,
            "included_in_package": item.has_premium_access,
            "points_remaining_after": remaining_if_enabled,
            "can_enable": item.is_selected or item.points <= menu.points_remaining,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_item(self._item_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_item(self._item_id, False)
