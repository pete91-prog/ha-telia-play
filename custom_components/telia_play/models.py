"""Selection-menu models for Telia Play TV points."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


class NotEnoughPointsError(Exception):
    """The requested services cost more points than the subscription includes."""

    def __init__(self, needed: int, remaining: int, total: int) -> None:
        self.needed = needed
        self.remaining = remaining
        self.total = total
        super().__init__(
            f"Not enough TV points: {needed} more needed, {remaining} remaining of {total}"
        )


class UnknownServiceError(Exception):
    """No selectable item matched the given name or id."""

    def __init__(self, query: str) -> None:
        self.query = query
        super().__init__(f"No Telia Play service matched '{query}'")


@dataclass(slots=True)
class StreamingFamily:
    """Public catalog family (Netflix, Max, …) with activation flags."""

    id: str
    name: str
    needs_activation: bool = False
    vas_hub_provisioning: bool = False
    service_ids: list[str] = field(default_factory=list)
    store_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CatalogItem:
    """A streaming service or channel package in the selection menu."""

    id: str
    name: str
    points: int
    vod: bool
    is_selected: bool
    has_premium_access: bool = False
    description: str | None = None
    logo: str | None = None
    activation_url: str | None = None
    family_id: str | None = None
    family_name: str | None = None
    vas_hub_provisioning: bool = False
    family_needs_activation: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return "streaming" if self.vod else "channel"

    @property
    def partner_id(self) -> str | None:
        return self.family_id

    @property
    def needs_activation(self) -> bool:
        return bool(
            self.is_selected
            and (self.activation_url or self.family_needs_activation)
        )


@dataclass(slots=True)
class SelectionMenu:
    """Current Valgmeny: included points and what is turned on."""

    points_total: int
    selectables: list[CatalogItem]
    fixed: list[CatalogItem] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def streaming_services(self) -> list[CatalogItem]:
        return [item for item in self.selectables if item.vod]

    @property
    def channels(self) -> list[CatalogItem]:
        return [item for item in self.selectables if not item.vod]

    @property
    def selected(self) -> list[CatalogItem]:
        return [item for item in self.selectables if item.is_selected]

    @property
    def selected_ids(self) -> list[str]:
        return [item.id for item in self.selected]

    @property
    def points_used(self) -> int:
        return sum(item.points for item in self.selected)

    @property
    def points_remaining(self) -> int:
        return max(0, self.points_total - self.points_used)

    def find(self, query: str) -> CatalogItem:
        item = match_item(self.selectables, query)
        if item is None:
            raise UnknownServiceError(query)
        return item


def match_item(items: list[CatalogItem], query: str) -> CatalogItem | None:
    """Match a service by id or name (exact, then fuzzy)."""
    if not query:
        return None
    needle = query.strip()
    lower = needle.lower()
    compact = lower.replace(" ", "").replace("+", "plus").replace("-", "")

    for item in items:
        if item.id == needle:
            return item
    for item in items:
        if item.name.lower() == lower:
            return item
    for item in items:
        name = item.name.lower().replace(" ", "").replace("+", "plus").replace("-", "")
        if name == compact:
            return item
    contains = [
        item
        for item in items
        if lower in item.name.lower()
        or compact in item.name.lower().replace(" ", "").replace("+", "plus")
    ]
    if len(contains) == 1:
        return contains[0]
    if contains:
        contains.sort(key=lambda item: (len(item.name), item.name.lower()))
        return contains[0]
    return None


def ids_after_toggle(menu: SelectionMenu, item_id: str, enabled: bool) -> list[str]:
    """Return the selectable ids that should be POSTed after a toggle."""
    item = next((entry for entry in menu.selectables if entry.id == item_id), None)
    if item is None:
        raise UnknownServiceError(item_id)
    selected = set(menu.selected_ids)
    if enabled:
        if item_id not in selected:
            extra = item.points
            if extra > menu.points_remaining:
                raise NotEnoughPointsError(extra, menu.points_remaining, menu.points_total)
            selected.add(item_id)
    else:
        selected.discard(item_id)
    return _ordered_ids(menu, selected)


def ids_for_streaming_names(
    menu: SelectionMenu, names: list[str], keep_channels: bool = True
) -> list[str]:
    """Replace the streaming-service selection, optionally keeping channels."""
    selected: set[str] = set()
    if keep_channels:
        selected.update(item.id for item in menu.channels if item.is_selected)
    for name in names:
        selected.add(menu.find(name).id)
    cost = sum(item.points for item in menu.selectables if item.id in selected)
    if cost > menu.points_total:
        raise NotEnoughPointsError(
            cost - menu.points_total, menu.points_remaining, menu.points_total
        )
    return _ordered_ids(menu, selected)


def _ordered_ids(menu: SelectionMenu, selected: set[str]) -> list[str]:
    return [item.id for item in menu.selectables if item.id in selected]


def item_from_api(raw: dict[str, Any]) -> CatalogItem:
    logos = raw.get("logos") or {}
    registration = raw.get("externalRegistration") or {}
    return CatalogItem(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or raw.get("id") or "Unknown"),
        points=int(raw.get("points") or 0),
        vod=bool(raw.get("vod")),
        is_selected=bool(raw.get("isSelected")),
        has_premium_access=bool(raw.get("hasPremiumAccess")),
        description=raw.get("description"),
        logo=_logo(logos),
        activation_url=registration.get("url"),
        extra={
            key: value
            for key, value in raw.items()
            if key
            not in {
                "id",
                "name",
                "points",
                "vod",
                "isSelected",
                "hasPremiumAccess",
                "description",
                "logos",
                "externalRegistration",
            }
        },
    )


def menu_from_api(raw: dict[str, Any]) -> SelectionMenu:
    selectables = [item_from_api(item) for item in raw.get("selectables") or [] if item.get("id")]
    fixed = [item_from_api(item) for item in raw.get("fixed") or [] if item.get("id")]
    return SelectionMenu(
        points_total=int(raw.get("pointsTotal") or 0),
        selectables=selectables,
        fixed=fixed,
        raw=raw,
    )


def normalize_username(value: str) -> str:
    """Keep email as typed (lowercased) and normalise Nordic mobile numbers."""
    raw = (value or "").strip()
    if not raw:
        return raw
    compact = raw.replace(" ", "").replace("-", "")
    digits = "".join(ch for ch in compact if ch.isdigit())
    if compact.startswith("+47") and len(digits) == 10:
        return "+47" + digits[2:]
    if digits.startswith("0047") and len(digits) == 12:
        return "+47" + digits[4:]
    if compact.startswith("47") and len(digits) == 10:
        return "+47" + digits[2:]
    if compact.isdigit() and len(digits) == 8:
        return digits
    if "@" in raw:
        return raw.lower()
    return raw.lower()


def username_candidates(value: str) -> list[str]:
    """Usernames Telia may accept for the same browser account."""
    raw = (value or "").strip()
    candidates: list[str] = []

    def add(item: str) -> None:
        item = (item or "").strip()
        if item and item not in candidates:
            candidates.append(item)

    if "@" in raw:
        add(raw.lower())
        add(raw)
    else:
        add(raw)
        add(raw.lower())
    normalised = normalize_username(raw)
    add(normalised)
    if normalised.isdigit() and len(normalised) == 8:
        add("+47" + normalised)
    if normalised.startswith("+47") and len(normalised) == 11:
        add(normalised[3:])
    return candidates


def extract_oauth_code(payload: Any) -> str | None:
    """Read the authorization code from /authenticate or a redirect URI."""
    if not isinstance(payload, dict):
        return None
    for key in ("code", "authorizationCode", "authorizationGrant"):
        value = payload.get(key)
        if value:
            return str(value)
    redirect = payload.get("redirectUri") or payload.get("redirect_uri")
    if not redirect:
        return None
    parsed = urlparse(str(redirect))
    query = parse_qs(parsed.query)
    for key in ("code", "authorizationCode"):
        values = query.get(key)
        if values and values[0]:
            return str(values[0])
    return None


def _logo(logos: dict[str, Any]) -> str | None:
    for theme in ("dark", "light"):
        node = logos.get(theme) or {}
        url = node.get("url") or node.get("sourceNonEncoded")
        if url:
            return unquote(str(url))
    return None


def families_from_api(
    families_raw: list[dict[str, Any]], services_raw: list[dict[str, Any]]
) -> list[StreamingFamily]:
    stores_by_family: dict[str, list[str]] = {}
    for service in services_raw:
        family_id = str(service.get("familyId") or "")
        if not family_id:
            continue
        stores_by_family.setdefault(family_id, [])
        for store_id in service.get("storeIds") or []:
            store = str(store_id)
            if store not in stores_by_family[family_id]:
                stores_by_family[family_id].append(store)
    families: list[StreamingFamily] = []
    for raw in families_raw:
        family_id = str(raw.get("id") or "")
        if not family_id:
            continue
        families.append(
            StreamingFamily(
                id=family_id,
                name=str(raw.get("name") or family_id),
                needs_activation=bool(raw.get("needsActivation")),
                vas_hub_provisioning=bool(raw.get("vasHubProvisioning")),
                service_ids=[str(value) for value in raw.get("tvStreamingServiceIds") or []],
                store_ids=stores_by_family.get(family_id, []),
            )
        )
    return families


def match_family(item: CatalogItem, families: list[StreamingFamily]) -> StreamingFamily | None:
    """Map a Valgmeny tile to its streaming-service family."""
    for family in families:
        if item.id in family.store_ids:
            return family
    name = item.name.lower()
    if "hbo" in name or name.strip() == "max":
        for family in families:
            if family.id == "7" or family.name.lower() == "max":
                return family
    best: StreamingFamily | None = None
    best_score = 0
    for family in families:
        family_name = family.name.lower()
        if family_name == "hbo" and any(
            other.id == "7" or other.name.lower() == "max" for other in families
        ):
            continue
        if family_name in name or name in family_name:
            score = len(family_name)
            if family_name == name:
                score += 10
            if score > best_score:
                best = family
                best_score = score
    return best


def enrich_menu(menu: SelectionMenu, families: list[StreamingFamily]) -> SelectionMenu:
    """Attach family/activation metadata to selectable streaming services."""
    for item in menu.selectables + menu.fixed:
        family = match_family(item, families) if item.vod else None
        if family is None:
            continue
        item.family_id = family.id
        item.family_name = family.name
        item.vas_hub_provisioning = family.vas_hub_provisioning
        item.family_needs_activation = family.needs_activation
    return menu


def vas_activation_url(services: Any, item: CatalogItem) -> str | None:
    """Pick an activation URL from /valueaddedservice/services if the menu had none."""
    entries = _vas_entries(services)
    if not entries:
        return None
    needles = [
        value.lower()
        for value in (item.name, item.family_name, item.id, item.family_id)
        if value
    ]
    for entry in entries:
        haystack = " ".join(
            str(entry.get(key) or "")
            for key in ("name", "label", "id", "familyName", "streamingServiceName")
        ).lower()
        if needles and not any(needle in haystack for needle in needles):
            continue
        for key in ("activationUrl", "activation_url", "url", "registrationUrl"):
            url = entry.get(key)
            if url:
                return str(url)
    return None


def _vas_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("services", "items", "valueAddedServices", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload]
