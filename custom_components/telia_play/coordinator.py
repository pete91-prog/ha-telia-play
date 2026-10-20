"""Coordinator for Telia Play Valgmeny."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .activation import (
    KIND_MAX,
    KIND_PRIME,
    STATUS_CODE_READY,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NEEDS_CODE,
    STATUS_STARTED,
    ActivationResult,
    activation_token,
    apply_partner_status,
    event_mail_text,
    extract_max_email_code,
    is_max_email,
    max_partner_ids,
    next_activation_target,
    parse_activation_input,
    partner_hint,
    partner_kind,
    remember_partner_url,
)
from .api import TeliaApiError, TeliaAuthError, TeliaPlayClient, TeliaPlayError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BOOT_ID,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_IMAP_FOLDER,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_USERNAME,
    CONF_INCLUDE_CHANNELS,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    CONF_VALID_TO,
    DEFAULT_IMAP_FOLDER,
    DEFAULT_IMAP_HOST,
    DEFAULT_IMAP_PORT,
    EVENT_IMAP_CONTENT,
    IMAP_POLL_SECONDS,
    NOTIFICATION_ID_PREFIX,
    SCAN_INTERVAL_SECONDS,
)
from .mail import MailError, fetch_recent_messages
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
        self._mail_started = False
        self._mail_lock = asyncio.Lock()
        self._imap_busy = False
        self._seen_mail_uids: set[str] = set()
        self._used_mail_codes: set[str] = set()

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

    @property
    def imap_configured(self) -> bool:
        options = self.entry.options
        return bool(options.get(CONF_IMAP_USERNAME) and options.get(CONF_IMAP_PASSWORD))

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
        target = next_activation_target(
            pasted_url=pasted.url,
            previous=previous,
            item_url=item.activation_url,
        )
        result = await self._async_open(item, url=target, code=pasted.code, refresh_spent=True)
        self.activations[item.id] = result
        self._notify_activation(result)
        await self._async_refresh_menu_quiet()
        return result

    def _resolve_activation_item(
        self, query: str | None, url: str | None
    ) -> CatalogItem:
        hint = (query or "").strip() or partner_hint(url)
        pending = [item for item in self.menu.streaming_services if item.needs_activation]
        if hint and hint.lower().replace(" ", "") in {"hbo", "hbomax", "max"}:
            for item in pending + [
                entry
                for entry in self.menu.streaming_services
                if entry.is_selected and entry not in pending
            ]:
                if partner_kind(
                    name=item.name,
                    family_name=item.family_name,
                    family_id=item.family_id,
                    url=item.activation_url,
                ) == KIND_MAX:
                    return item
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
        result = await self._async_open(
            item, url=item.activation_url, code=None, refresh_spent=True
        )
        self.activations[item.id] = result
        if notify:
            self._notify_activation(result)
        self.async_update_listeners()
        if result.status in {STATUS_NEEDS_CODE, STATUS_STARTED}:
            self._schedule_mail_poll()
        return result

    async def _async_activate_pending(self, menu: SelectionMenu, *, notify: bool) -> None:
        for item in menu.streaming_services:
            if item.needs_activation:
                try:
                    await self.async_activate_item(item, notify=notify)
                except TeliaPlayError as err:
                    _LOGGER.warning("Could not start activation for %s: %s", item.name, err)

    async def _async_open(
        self,
        item: CatalogItem,
        *,
        url: str | None,
        code: str | None,
        refresh_spent: bool = False,
    ) -> ActivationResult:
        activation_url = url
        if not activation_url:
            vas = await self.client.async_value_added_services()
            activation_url = vas_activation_url(vas, item)
        kind = partner_kind(
            name=item.name,
            family_name=item.family_name,
            family_id=item.family_id,
            url=activation_url,
        )
        partner_token = None
        extra_ids = max_partner_ids(item.family_id) if kind == KIND_MAX else None
        if item.partner_id or extra_ids:
            partner_token = await self.client.async_partner_token(
                item.partner_id or (extra_ids[0] if extra_ids else ""),
                extra_ids=extra_ids,
            )
        if not activation_url:
            status = STATUS_NEEDS_CODE if code is None else STATUS_FAILED
            if kind == KIND_MAX:
                message = (
                    "HBO / Max is not finished yet. Paste the SMS link from "
                    "activate.teliacompany.com, or press Activate to ask Telia for a new "
                    "one if that link was already used."
                )
            elif kind == KIND_PRIME:
                status = STATUS_COMPLETED
                message = (
                    f"{item.name} is enabled. Prime normally activates through Telia "
                    "with no SMS or email code."
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
        previous = self.activations.get(item.id)
        try:
            opened = await self.client.async_open_activation_url(
                activation_url,
                partner_token=partner_token,
                code=code,
                email=email,
                force_email=kind == KIND_MAX,
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

        if opened.get("spent") and refresh_spent and kind == KIND_MAX:
            fresh = await self._async_fresh_max_url(
                item, opened.get("requested_url") or activation_url
            )
            if fresh:
                return await self._async_open(
                    item, url=fresh, code=code, refresh_spent=False
                )

        status = apply_partner_status(
            str(opened.get("status") or STATUS_STARTED),
            kind=kind,
            http_status=opened.get("http_status"),
        )
        pairing = opened.get("pairing_code")
        partner_url = remember_partner_url(
            opened.get("requested_url") or activation_url,
            opened.get("url"),
            previous.partner_url if previous else None,
        )
        opened_url = partner_url or opened.get("url") or activation_url
        if status == STATUS_COMPLETED:
            if kind == KIND_PRIME:
                message = (
                    f"{item.name} activated through Telia. No SMS or email code was needed."
                )
            elif kind == KIND_MAX:
                message = (
                    f"{item.name} activation finished. You can sign in on Max with the "
                    "same email you used for the one-time code."
                )
            else:
                message = (
                    f"{item.name} activation finished on the Telia side. No email code was needed."
                )
        elif status == STATUS_CODE_READY and pairing:
            message = (
                f"{item.name} is ready to finish. Telia already issued this code "
                f"(no need to wait for mail): {pairing}"
            )
        elif status == STATUS_FAILED:
            message = f"Could not open the {item.name} activation link."
        elif status == STATUS_NEEDS_CODE:
            if kind == KIND_MAX and opened.get("spent"):
                message = (
                    "That Telia Max SMS link is already used. The email code from that "
                    "open only works on the phone where you opened it. Press Activate "
                    "HBO/Max to ask Telia for a new link, or finish Max on that phone."
                )
            elif kind == KIND_MAX:
                if self.imap_configured:
                    message = (
                        "HBO / Max is waiting for the email one-time code. Home Assistant "
                        "is watching your mailbox and will submit it automatically. Use "
                        "the same address on the Max page as that inbox."
                    )
                else:
                    message = (
                        "HBO / Max is not finished yet. Home Assistant will submit the "
                        "6-digit Max email code automatically if this inbox is connected "
                        "(Telia Play options, or Home Assistant's IMAP integration). Use "
                        "the same email on the Max page as that mailbox. You can still "
                        "paste the SMS https link or the code. If the SMS link was already "
                        "opened on the phone, finish it there or press Activate for a new "
                        "Telia link."
                    )
            else:
                message = (
                    f"{item.name} still wants a partner code. Open the activation link "
                    "from this notification. If a code arrives by SMS or email, paste it "
                    "with telia_play.submit_activation_code."
                )
        else:
            message = (
                f"Opened the {item.name} activation link from Telia’s API instead of "
                "waiting for email. Tap the link if the partner still asks you to confirm "
                "the account (Netflix login is theirs, not Telia’s)."
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
            partner_url=partner_url,
            extra={
                "http_status": opened.get("http_status"),
                "spent": bool(opened.get("spent")),
            },
        )

    async def _async_fresh_max_url(self, item: CatalogItem, used_url: str | None) -> str | None:
        """Ask Telia for a new startactivation link after the SMS UUID is spent."""
        used_token = activation_token(used_url)
        await self._async_refresh_menu_quiet()
        current = next(
            (entry for entry in self.menu.streaming_services if entry.id == item.id),
            None,
        )
        if current is None:
            try:
                current = self.menu.find(item.name)
            except UnknownServiceError:
                current = item
        fresh = current.activation_url
        if fresh and activation_token(fresh) != used_token:
            item.activation_url = fresh
            return fresh
        try:
            menu = await self.client.async_save_selection(self.menu.selected_ids)
            families = await self.client.async_streaming_families()
            menu = enrich_menu(menu, families)
            self.async_set_updated_data(menu)
        except TeliaPlayError as err:
            _LOGGER.debug("Could not refresh Max activation URL: %s", err)
            return None
        current = next(
            (entry for entry in menu.streaming_services if entry.id == item.id),
            current,
        )
        fresh = current.activation_url if current else None
        if fresh and activation_token(fresh) != used_token:
            item.activation_url = fresh
            return fresh
        return None

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

    def async_start_mail(self) -> None:
        """Watch a user-owned mailbox and Home Assistant IMAP events for Max codes."""
        if self._mail_started:
            return
        self._mail_started = True
        self.entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_IMAP_CONTENT, self.async_imap_content)
        )
        self.entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self.async_poll_imap,
                timedelta(seconds=IMAP_POLL_SECONDS),
            )
        )
        self._schedule_mail_poll()

    def _schedule_mail_poll(self) -> None:
        if self.hass.is_stopping:
            return
        self.hass.async_create_task(self.async_poll_imap())

    def _max_waiting_for_code(self) -> bool:
        if self.data is None:
            return False
        for item in self.data.streaming_services:
            kind = partner_kind(
                name=item.name,
                family_name=item.family_name,
                family_id=item.family_id,
                url=item.activation_url,
            )
            if kind != KIND_MAX:
                continue
            result = self.activations.get(item.id)
            if result is not None and result.status in {
                STATUS_NEEDS_CODE,
                STATUS_STARTED,
                STATUS_CODE_READY,
            }:
                return True
            if item.needs_activation:
                return True
        return False

    async def async_imap_content(self, event: Event) -> None:
        """Home Assistant IMAP integration delivered a new message."""
        if not self._max_waiting_for_code():
            return
        data = event.data or {}
        sender = str(data.get("sender") or "")
        subject = str(data.get("subject") or "")
        body = event_mail_text(data)
        if not is_max_email(sender=sender, subject=subject, body=body):
            return
        uid = str(data.get("uid") or "")
        if uid:
            if uid in self._seen_mail_uids:
                return
            self._seen_mail_uids.add(uid)
        code = extract_max_email_code(f"{subject}\n{body}")
        if code:
            await self._async_ingest_mail_code(code, source="imap_content")

    async def async_poll_imap(self, _now=None) -> None:
        """Read the configured inbox only while Max still needs a code."""
        if not self.imap_configured or not self._max_waiting_for_code() or self._imap_busy:
            return
        self._imap_busy = True
        try:
            options = self.entry.options
            messages = await self.hass.async_add_executor_job(
                fetch_recent_messages,
                str(options.get(CONF_IMAP_HOST) or DEFAULT_IMAP_HOST),
                int(options.get(CONF_IMAP_PORT) or DEFAULT_IMAP_PORT),
                str(options.get(CONF_IMAP_USERNAME) or ""),
                str(options.get(CONF_IMAP_PASSWORD) or ""),
                str(options.get(CONF_IMAP_FOLDER) or DEFAULT_IMAP_FOLDER),
            )
            for message in messages:
                if message.uid in self._seen_mail_uids:
                    continue
                self._seen_mail_uids.add(message.uid)
                if not is_max_email(
                    sender=message.sender,
                    subject=message.subject,
                    body=message.body,
                ):
                    continue
                code = extract_max_email_code(f"{message.subject}\n{message.body}")
                if not code:
                    continue
                await self._async_ingest_mail_code(code, source="imap")
                break
        except MailError as err:
            _LOGGER.warning("Max mailbox poll failed: %s", err)
        except Exception as err:  # noqa: BLE001 - never fail the coordinator on mail
            _LOGGER.warning("Max mailbox poll failed: %s", err)
        finally:
            self._imap_busy = False

    async def _async_ingest_mail_code(self, code: str, *, source: str) -> None:
        async with self._mail_lock:
            if code in self._used_mail_codes or not self._max_waiting_for_code():
                return
            self._used_mail_codes.add(code)
            if len(self._used_mail_codes) > 20:
                self._used_mail_codes = set(list(self._used_mail_codes)[-20:])
            _LOGGER.info("Submitting Max one-time code from %s", source)
            try:
                result = await self.async_submit_code("Max", code)
            except HomeAssistantError as err:
                _LOGGER.warning("Could not submit Max mail code: %s", err)
                self._used_mail_codes.discard(code)
                return
            if result.status == STATUS_COMPLETED:
                _LOGGER.info("Max activation finished from %s", source)
