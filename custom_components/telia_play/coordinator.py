"""Coordinator for Telia Play Valgmeny."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .activation import (
    STATUS_CODE_READY,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NEEDS_CODE,
    ActivationResult,
    parse_activation_input,
    partner_hint,
)
from .api import TeliaApiError, TeliaAuthError, TeliaPlayClient, TeliaPlayError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BOOT_ID,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_INCLUDE_CHANNELS,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    CONF_VALID_TO,
    NOTIFICATION_ID_PREFIX,
    SCAN_INTERVAL_SECONDS,
)
from .models import (
    CatalogItem,
    NotEnoughPointsError,
    SelectionMenu,
    UnknownServiceError,
    enrich_menu,
    ids_after_toggle,
    ids_for_streaming_names,
    match_item,
    vas_activation_url,
)

_LOGGER = logging.getLogger(__name__)


class TeliaPlayCoordinator(DataUpdateCoordinator[SelectionMenu]):
    """Keeps the selection menu and applies TV-point changes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.client = TeliaPlayClient(
            session=async_get_clientsession(hass),
            country=entry.data[CONF_COUNTRY],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            device_id=entry.data.get(CONF_DEVICE_ID),
            boot_id=entry.data.get(CONF_BOOT_ID),
            access_token=entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
            valid_to=entry.data.get(CONF_VALID_TO),
        )
        self.activations: dict[str, ActivationResult] = {}
        self._bootstrapped_activation = False

    @property
    def menu(self) -> SelectionMenu:
        if self.data is None:
            raise HomeAssistantError("Telia Play selection menu has not loaded yet")
        return self.data

    @property
    def include_channels(self) -> bool:
        return bool(
            self.entry.options.get(CONF_INCLUDE_CHANNELS)
            or self.entry.data.get(CONF_INCLUDE_CHANNELS)
        )

    def items_for_switches(self) -> list[CatalogItem]:
        if self.data is None:
            return []
        items = list(self.data.streaming_services)
        if self.include_channels:
            items.extend(self.data.channels)
        return items

    def activation_for(self, item_id: str) -> ActivationResult | None:
        return self.activations.get(item_id)

    async def _async_update_data(self) -> SelectionMenu:
        try:
            menu = await self._async_load_menu()
        except TeliaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TeliaApiError as err:
            raise UpdateFailed(str(err)) from err
        await self._async_persist_tokens()
        if not self._bootstrapped_activation:
            self._bootstrapped_activation = True
            await self._async_activate_pending(menu, notify=True)
        return menu

    async def _async_load_menu(self) -> SelectionMenu:
        menu = await self.client.async_selection_menu()
        try:
            families = await self.client.async_streaming_families()
        except TeliaPlayError as err:  # pragma: no cover - catalog is public
            _LOGGER.debug("Streaming families unavailable: %s", err)
            families = []
        return enrich_menu(menu, families)

    async def _async_persist_tokens(self) -> None:
        payload = self.client.token_payload()
        current = {key: self.entry.data.get(key, "") for key in payload}
        if current == payload:
            return
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, **payload}
        )

    async def async_set_item(self, item_id: str, enabled: bool) -> SelectionMenu:
        try:
            ids = ids_after_toggle(self.menu, item_id, enabled)
            menu = await self.client.async_save_selection(ids)
            families = await self.client.async_streaming_families()
            menu = enrich_menu(menu, families)
        except NotEnoughPointsError as err:
            raise HomeAssistantError(str(err)) from err
        except UnknownServiceError as err:
            raise HomeAssistantError(str(err)) from err
        except TeliaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TeliaApiError as err:
            raise HomeAssistantError(str(err)) from err
        self.async_set_updated_data(menu)
        await self._async_persist_tokens()
        if enabled:
            item = next((entry for entry in menu.selectables if entry.id == item_id), None)
            if item and item.needs_activation:
                await self.async_activate_item(item, notify=True)
        else:
            self.activations.pop(item_id, None)
            persistent_notification.async_dismiss(
                self.hass, f"{NOTIFICATION_ID_PREFIX}_{item_id}"
            )
        return self.data or menu

    async def async_set_named(self, query: str, enabled: bool) -> SelectionMenu:
        try:
            item = self.menu.find(query)
        except UnknownServiceError as err:
            raise HomeAssistantError(str(err)) from err
        return await self.async_set_item(item.id, enabled)

    async def async_set_streaming_services(self, names: list[str]) -> SelectionMenu:
        previous = {item.id for item in self.menu.streaming_services if item.is_selected}
        try:
            ids = ids_for_streaming_names(self.menu, names, keep_channels=True)
            menu = await self.client.async_save_selection(ids)
            families = await self.client.async_streaming_families()
            menu = enrich_menu(menu, families)
        except NotEnoughPointsError as err:
            raise HomeAssistantError(str(err)) from err
        except UnknownServiceError as err:
            raise HomeAssistantError(str(err)) from err
        except TeliaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TeliaApiError as err:
            raise HomeAssistantError(str(err)) from err
        self.async_set_updated_data(menu)
        await self._async_persist_tokens()
        newly = [
            item
            for item in menu.streaming_services
            if item.is_selected and item.id not in previous and item.needs_activation
        ]
        for item in newly:
            await self.async_activate_item(item, notify=True)
        return self.data or menu

    async def async_activate_named(self, query: str) -> ActivationResult:
        try:
            item = self.menu.find(query)
        except UnknownServiceError as err:
            raise HomeAssistantError(str(err)) from err
        return await self.async_activate_item(item, notify=True)

    async def async_submit_code(
        self, query: str | None, code: str
    ) -> ActivationResult:
        value = (code or "").strip()
        if not value:
            raise HomeAssistantError("Activation code is empty")
        pasted = parse_activation_input(value)
        item = self._resolve_activation_item(query, pasted.url)
        previous = self.activations.get(item.id)
        target = (
            pasted.url
            or (previous.activation_url if previous else None)
            or item.activation_url
        )
        result = await self._async_open(item, url=target, code=pasted.code)
        self.activations[item.id] = result
        self._notify_activation(result)
        await self._async_refresh_menu_quiet()
        return result

    def _resolve_activation_item(
        self, query: str | None, url: str | None
    ) -> CatalogItem:
        hint = (query or "").strip() or partner_hint(url)
        pending = [item for item in self.menu.streaming_services if item.needs_activation]
        if hint:
            try:
                return self.menu.find(hint)
            except UnknownServiceError:
                matched = match_item(pending, hint)
                if matched:
                    return matched
        if len(pending) == 1:
            return pending[0]
        if hint:
            raise HomeAssistantError(f"No Telia Play service matched '{hint}'")
        names = ", ".join(item.name for item in pending) or "none"
        raise HomeAssistantError(
            "Say which service the SMS/email belongs to "
            f"(pending: {names}), or paste the full Max link."
        )

    async def async_activate_item(
        self, item: CatalogItem, *, notify: bool = True
    ) -> ActivationResult:
        result = await self._async_open(item, url=item.activation_url, code=None)
        self.activations[item.id] = result
        if notify:
            self._notify_activation(result)
        self.async_update_listeners()
        return result

    async def _async_activate_pending(self, menu: SelectionMenu, *, notify: bool) -> None:
        for item in menu.streaming_services:
            if item.needs_activation:
                try:
                    await self.async_activate_item(item, notify=notify)
                except TeliaPlayError as err:
                    _LOGGER.warning("Could not start activation for %s: %s", item.name, err)

    async def _async_open(
        self, item: CatalogItem, *, url: str | None, code: str | None
    ) -> ActivationResult:
        activation_url = url
        if not activation_url:
            vas = await self.client.async_value_added_services()
            activation_url = vas_activation_url(vas, item)
        partner_token = None
        if item.partner_id:
            partner_token = await self.client.async_partner_token(item.partner_id)

        if not activation_url:
            status = STATUS_NEEDS_CODE if code is None else STATUS_FAILED
            if _is_max_item(item):
                message = (
                    "Max sent an SMS with a link, then an email with a one-time code. "
                    "Paste the SMS link into the Activation code box, then paste the "
                    "6-digit email code. You do not need to wait for Telia’s mail."
                )
            else:
                message = (
                    f"{item.name} is enabled with TV points, but Telia has not issued an "
                    "activation link yet. If a code arrives, submit it with "
                    "telia_play.submit_activation_code."
                )
            return ActivationResult(
                item_id=item.id,
                name=item.name,
                status=status,
                message=message,
                family_id=item.family_id,
            )

        email = self.client.username if "@" in (self.client.username or "") else None
        try:
            opened = await self.client.async_open_activation_url(
                activation_url,
                partner_token=partner_token,
                code=code,
                email=email,
            )
        except TeliaApiError as err:
            return ActivationResult(
                item_id=item.id,
                name=item.name,
                status=STATUS_FAILED,
                message=str(err),
                activation_url=activation_url,
                family_id=item.family_id,
            )

        status = str(opened.get("status") or STATUS_STARTED)
        pairing = opened.get("pairing_code")
        opened_url = opened.get("url") or activation_url
        max_like = _is_max_item(item)
        if status == STATUS_COMPLETED:
            message = (
                f"{item.name} activation finished. You can sign in on Max with the "
                "same email you used for the one-time code."
                if max_like
                else f"{item.name} activation finished on the Telia side. No email code was needed."
            )
        elif status == STATUS_CODE_READY and pairing:
            message = (
                f"{item.name} is ready to finish. Telia already issued this code "
                f"(no need to wait for mail): {pairing}"
            )
        elif status == STATUS_FAILED:
            message = f"Could not open the {item.name} activation link."
        elif status == STATUS_NEEDS_CODE or max_like:
            message = (
                "Max sent an SMS with a link, then an email with a one-time code. "
                "Paste the SMS link into the Activation code box (or "
                "telia_play.submit_activation_code), then paste the 6-digit email code."
                if max_like
                else (
                    f"{item.name} still wants a partner code. Open the activation link "
                    "from this notification. If a code arrives by SMS or email, paste it "
                    "with telia_play.submit_activation_code."
                )
            )
            if max_like and status == STATUS_STARTED:
                status = STATUS_NEEDS_CODE
        else:
            message = (
                f"Opened the {item.name} activation link from Telia’s API instead of "
                "waiting for email. Tap the link if the partner still asks you to confirm "
                "the account (Netflix/Max login is theirs, not Telia’s)."
            )
        return ActivationResult(
            item_id=item.id,
            name=item.name,
            status=status,
            message=message,
            activation_url=opened_url,
            pairing_code=pairing if isinstance(pairing, str) else None,
            family_id=item.family_id,
            final_url=opened.get("url"),
            extra={"http_status": opened.get("http_status")},
        )

    async def _async_refresh_menu_quiet(self) -> None:
        try:
            menu = await self._async_load_menu()
        except TeliaPlayError as err:
            _LOGGER.debug("Menu refresh after activation failed: %s", err)
            return
        self.async_set_updated_data(menu)
        await self._async_persist_tokens()

    def _notify_activation(self, result: ActivationResult) -> None:
        notification_id = f"{NOTIFICATION_ID_PREFIX}_{result.item_id}"
        if result.status == STATUS_COMPLETED and not result.pairing_code:
            persistent_notification.async_dismiss(self.hass, notification_id)
            persistent_notification.async_create(
                self.hass,
                result.message,
                title=f"{result.name} activated",
                notification_id=notification_id,
            )
            return
        lines = [result.message]
        if result.pairing_code:
            lines.append(f"Code: `{result.pairing_code}`")
        if result.activation_url:
            lines.append(f"[Open activation]({result.activation_url})")
        persistent_notification.async_create(
            self.hass,
            "\n\n".join(lines),
            title=f"Activate {result.name}",
            notification_id=notification_id,
        )


def _is_max_item(item: CatalogItem) -> bool:
    blob = f"{item.name} {item.family_name or ''}".lower()
    return "max" in blob or blob.strip() == "hbo"
