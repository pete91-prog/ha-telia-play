"""Helpers that start partner activation without waiting for email."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import re

from .const import TELIA_AUTH_HOST_SUFFIXES

STATUS_COMPLETED = "completed"
STATUS_STARTED = "started"
STATUS_CODE_READY = "code_ready"
STATUS_NEEDS_CODE = "needs_code"
STATUS_FAILED = "failed"

KIND_MAX = "max"
KIND_PRIME = "prime"
KIND_NETFLIX = "netflix"
KIND_VIAPLAY = "viaplay"
KIND_DISNEY = "disney"

_CODE_PATTERNS = (
    re.compile(
        r'["\'](?:activation)?(?:code|kode|otp|pin)["\']\s*[:=]\s*["\']([A-Za-z0-9]{4,12})["\']',
        re.I,
    ),
    re.compile(r'data-(?:activation-)?code=["\']([A-Za-z0-9]{4,12})["\']', re.I),
    re.compile(
        r"(?:activation code|aktiveringskode|your code|koden din|"
        r"enter this code|bruk denne koden|pairing code|tv-kode)"
        r"[^\w]{0,48}([A-Z0-9]{4,8})",
        re.I,
    ),
)

_SUCCESS_HINTS = (
    "activated",
    "activation-complete",
    "already-active",
    "success",
    "confirmed",
    "fullfort",
    "fullført",
    "aktivert",
    "start streaming",
    "ready to stream",
    "you're ready",
    "you are ready",
)
_PENDING_HINTS = (
    "enter code",
    "enter the code",
    "skriv inn",
    "aktiveringskode",
    "verification code",
    "verify",
    "one-time",
    "otp",
    "one time code",
    "one-time code",
)
_EMAIL_HINTS = (
    "enter your email",
    "enter email",
    "e-post",
    "email address",
    "verify email",
)

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.I)
_LABELED_CODE = re.compile(
    r"(?:one[- ]time code|activation code|aktiveringskode|your code|koden din|"
    r"kode|code|otp|pin)\s*[:=]?\s*([A-Za-z0-9]{4,12})",
    re.I,
)
_SIX_DIGIT = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_BARE_CODE = re.compile(r"^[A-Za-z0-9]{4,12}$")

MAX_HOST_SUFFIXES = (
    "max.com",
    "hbomax.com",
    "hbo.com",
    "play.max.com",
)
MAX_FAMILY_IDS = ("7", "20")
_FOLLOW_URL = re.compile(r"https://[^\s\"'<>\\]+", re.I)
_ACTIVATION_KEYS = ("activate", "token=", "partner", "register", "provision", "connect")


@dataclass(slots=True)
class ActivationResult:
    """Outcome of opening a Telia / partner activation link."""

    item_id: str
    name: str
    status: str
    message: str
    activation_url: str | None = None
    pairing_code: str | None = None
    family_id: str | None = None
    final_url: str | None = None
    partner_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status == STATUS_COMPLETED

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "activation_url": self.activation_url,
            "partner_url": self.partner_url,
            "pairing_code": self.pairing_code,
            "family_id": self.family_id,
        }


@dataclass(slots=True)
class ActivationInput:
    """SMS link and/or email code pasted from Max / Telia."""

    raw: str
    url: str | None = None
    code: str | None = None


def is_telia_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in TELIA_AUTH_HOST_SUFFIXES)


def looks_like_url(value: str) -> bool:
    text = value.strip()
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def is_max_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in MAX_HOST_SUFFIXES)


def partner_hint(url: str | None) -> str | None:
    """Guess which streaming service an SMS/email link belongs to."""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    path = (urlparse(url).path or "").lower()
    blob = f"{host} {path}"
    if is_max_host(url) or "hbo" in blob:
        return KIND_MAX
    if "netflix" in blob:
        return KIND_NETFLIX
    if "viaplay" in blob:
        return KIND_VIAPLAY
    if "disney" in blob:
        return KIND_DISNEY
    if "prime" in blob or "amazon" in blob or "primevideo" in blob:
        return KIND_PRIME
    return None


def partner_kind(
    *,
    name: str = "",
    family_name: str | None = None,
    family_id: str | None = None,
    url: str | None = None,
) -> str | None:
    """Classify a Valgmeny tile as Max, Prime, etc."""
    title = (name or "").lower()
    family = (family_name or "").lower()
    fid = str(family_id or "")
    if fid in MAX_FAMILY_IDS or family in {"max", "hbo"} or "hbo" in title or _has_max_word(title):
        return KIND_MAX
    if url and is_max_host(url):
        return KIND_MAX
    if fid == "8" or family in {"amazon", "prime"} or "prime" in title or "amazon" in title:
        return KIND_PRIME
    if fid == "6" or "netflix" in title or family == "netflix":
        return KIND_NETFLIX
    return partner_hint(url)


def _has_max_word(value: str) -> bool:
    return bool(re.search(r"\bmax\b", value or "", re.I))


def is_activation_link(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    blob = f"{parsed.path} {parsed.query} {parsed.hostname or ''}".lower()
    if "login" in parsed.path.lower() and "activate" not in blob:
        return False
    return any(key in blob for key in _ACTIVATION_KEYS) or is_max_host(url)


def remember_partner_url(
    original: str | None,
    final: str | None = None,
    previous: str | None = None,
) -> str | None:
    """Keep the Max SMS / Telia activate link, not the post-redirect login page."""
    candidates = [original, previous, final]
    for url in candidates:
        if url and is_max_host(url) and is_activation_link(url):
            return url
    for url in candidates:
        if url and is_activation_link(url):
            return url
    for url in candidates:
        if url:
            return url
    return None


def next_activation_target(
    *,
    pasted_url: str | None,
    previous: ActivationResult | None,
    item_url: str | None,
) -> str | None:
    prev_url = None
    if previous is not None:
        prev_url = previous.partner_url or previous.activation_url
    return pasted_url or prev_url or item_url


def extract_follow_urls(body: str | None, base: str | None = None) -> list[str]:
    """Pull Max / activate links out of Telia HTML or JSON."""
    found: list[str] = []
    for match in _FOLLOW_URL.finditer(body or ""):
        url = match.group(0).rstrip(").,]>\"'")
        if not looks_like_url(url):
            continue
        if is_max_host(url) or is_activation_link(url) or is_telia_host(url):
            if url not in found:
                found.append(url)
    return found


def max_partner_ids(family_id: str | None) -> list[str]:
    ids: list[str] = []
    for value in (family_id, *MAX_FAMILY_IDS):
        if value and value not in ids:
            ids.append(value)
    return ids


def apply_partner_status(
    status: str, *, kind: str | None, http_status: int | None
) -> str:
    """Prime finishes on Telia's link; Max still needs the SMS + email OTP."""
    if kind == KIND_PRIME and status == STATUS_STARTED:
        if http_status is None or 200 <= int(http_status) < 400:
            return STATUS_COMPLETED
    if kind == KIND_MAX and status == STATUS_STARTED:
        return STATUS_NEEDS_CODE
    return status


def wants_email(body: str | None, final_url: str | None = None) -> bool:
    blob = f"{final_url or ''} {body or ''}".lower()
    return any(hint in blob for hint in _EMAIL_HINTS)


def parse_activation_input(value: str) -> ActivationInput:
    """Pull a Max SMS link and/or the email one-time code out of pasted text."""
    text = (value or "").strip()
    parsed = ActivationInput(raw=text)
    if not text:
        return parsed
    if looks_like_url(text):
        parsed.url = text
        return parsed
    match = _URL_IN_TEXT.search(text)
    if match:
        parsed.url = match.group(0).rstrip(").,]>\"'")
    labeled = _LABELED_CODE.search(text)
    if labeled:
        parsed.code = labeled.group(1)
    elif parsed.url is None and _BARE_CODE.fullmatch(text):
        parsed.code = text
    elif parsed.url is None:
        six = _SIX_DIGIT.search(text)
        if six:
            parsed.code = six.group(1)
    return parsed


def with_query(url: str, **params: str | None) -> str:
    """Add query params without dropping existing ones."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        if value:
            query[key] = [value]
    encoded = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=encoded))


def with_partner_token(url: str, token: str | None) -> str:
    if not url or not token:
        return url
    return with_query(url, partnerToken=token, token=token)


def extract_activation_code(text: str | None) -> str | None:
    """Pull a pairing / activation code out of HTML or JSON if Telia already issued one."""
    if not text:
        return None
    for pattern in _CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            code = match.group(1).strip()
            if code.lower() not in {"null", "true", "false", "code", "kode"}:
                return code
    return None


def infer_status(*, final_url: str | None, body: str | None, http_status: int | None) -> str:
    blob = f"{final_url or ''} {body or ''}".lower()
    if any(hint in blob for hint in _SUCCESS_HINTS) and "enter" not in blob:
        return STATUS_COMPLETED
    if extract_activation_code(body):
        return STATUS_CODE_READY
    if wants_email(body, final_url) or any(hint in blob for hint in _PENDING_HINTS):
        return STATUS_NEEDS_CODE
    if http_status is not None and http_status >= 400:
        return STATUS_FAILED
    if http_status == 200:
        return STATUS_STARTED
    return STATUS_STARTED


def code_payloads(code: str) -> list[dict[str, str]]:
    """Bodies Telia / Max pages commonly accept for a pasted activation code."""
    return [
        {"code": code},
        {"activationCode": code},
        {"activation_code": code},
        {"oneTimeCode": code},
        {"otp": code},
        {"otpCode": code},
        {"verificationCode": code},
        {"verification_code": code},
        {"pinCode": code},
        {"pin": code},
        {"token": code},
    ]


def email_payloads(email: str) -> list[dict[str, str]]:
    return [
        {"email": email},
        {"emailAddress": email},
        {"username": email},
        {"login": email},
    ]
