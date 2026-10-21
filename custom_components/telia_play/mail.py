"""Read Max one-time codes from a mailbox the user owns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
import imaplib
import ssl

from .const import DEFAULT_IMAP_FOLDER


class MailError(Exception):
    """Mailbox login or fetch failed."""


@dataclass(slots=True)
class MailMessage:
    """One IMAP message, already decoded."""

    uid: str
    sender: str
    subject: str
    body: str


def probe_imap(
    host: str,
    port: int,
    username: str,
    password: str,
    folder: str = DEFAULT_IMAP_FOLDER,
) -> None:
    """Login and open the folder so options can fail fast."""
    client = _connect(host, port, username, password)
    try:
        typ, _ = client.select(folder, readonly=True)
        if typ != "OK":
            raise MailError(f"Could not open folder {folder}")
    finally:
        _logout(client)


def fetch_recent_messages(
    host: str,
    port: int,
    username: str,
    password: str,
    folder: str = DEFAULT_IMAP_FOLDER,
    limit: int = 25,
) -> list[MailMessage]:
    """Newest-first messages from Max senders, plus unseen mail to filter locally."""
    client = _connect(host, port, username, password)
    try:
        typ, _ = client.select(folder, readonly=True)
        if typ != "OK":
            raise MailError(f"Could not open folder {folder}")
        uids = _collect_uids(client)
        messages: list[MailMessage] = []
        for uid in uids[:limit]:
            raw = _fetch_message(client, uid)
            if raw is None:
                continue
            parsed = message_from_bytes(raw)
            messages.append(
                MailMessage(
                    uid=uid,
                    sender=_header(parsed, "From"),
                    subject=_header(parsed, "Subject"),
                    body=_body(parsed),
                )
            )
        return messages
    except MailError:
        raise
    except imaplib.IMAP4.error as err:
        raise MailError("Mailbox search failed") from err
    finally:
        _logout(client)


def _connect(host: str, port: int, username: str, password: str) -> imaplib.IMAP4_SSL:
    if not host or not username or not password:
        raise MailError("Mailbox host, username, and password are required")
    context = ssl.create_default_context()
    try:
        client = imaplib.IMAP4_SSL(host, int(port), timeout=25, ssl_context=context)
        client.login(username, password)
    except imaplib.IMAP4.error as err:
        raise MailError("Mailbox login failed") from err
    except OSError as err:
        raise MailError("Could not reach the mailbox server") from err
    return client


def _logout(client: imaplib.IMAP4) -> None:
    try:
        client.logout()
    except (imaplib.IMAP4.error, OSError):
        try:
            client.shutdown()
        except (imaplib.IMAP4.error, OSError):
            pass


def _collect_uids(client: imaplib.IMAP4) -> list[str]:
    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%d-%b-%Y")
    found: list[str] = []
    seen: set[str] = set()
    queries = (
        ("UNSEEN",),
        ("FROM", "max.com", "SINCE", since),
        ("FROM", "hbomax.com", "SINCE", since),
        ("FROM", "hbo.com", "SINCE", since),
        ("FROM", "email.max.com", "SINCE", since),
        ("SUBJECT", "Max", "SINCE", since),
    )
    for criteria in queries:
        for uid in _search_uids(client, *criteria):
            if uid not in seen:
                seen.add(uid)
                found.append(uid)
    found.sort(key=_uid_sort, reverse=True)
    return found


def _uid_sort(uid: str) -> int:
    try:
        return int(uid)
    except ValueError:
        return 0


def _search_uids(client: imaplib.IMAP4, *criteria: str) -> list[str]:
    try:
        typ, data = client.uid("SEARCH", None, *criteria)
    except imaplib.IMAP4.error:
        return []
    if typ != "OK" or not data or not data[0]:
        return []
    tokens = data[0].split()
    uids: list[str] = []
    for token in tokens:
        uid = token.decode() if isinstance(token, bytes) else str(token)
        if uid:
            uids.append(uid)
    return uids


def _fetch_message(client: imaplib.IMAP4, uid: str) -> bytes | None:
    try:
        typ, data = client.uid("FETCH", uid, "(BODY.PEEK[])")
    except imaplib.IMAP4.error:
        return None
    if typ != "OK":
        return None
    return _message_bytes(data)


def _message_bytes(data: object) -> bytes | None:
    if isinstance(data, bytes):
        if b"\n" in data or data.lower().startswith(b"from:"):
            return data
        return None
    if isinstance(data, tuple):
        for part in data:
            found = _message_bytes(part)
            if found:
                return found
        return None
    if isinstance(data, list):
        for part in data:
            found = _message_bytes(part)
            if found:
                return found
    return None


def _header(message: Message, name: str) -> str:
    raw = message.get(name, "")
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except (LookupError, UnicodeError, TypeError, ValueError):
        return str(raw)


def _body(message: Message) -> str:
    texts: list[str] = []
    htmls: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                texts.append(_decode_part(part))
            elif ctype == "text/html":
                htmls.append(_decode_part(part))
        return "\n".join(part for part in texts if part) or "\n".join(
            part for part in htmls if part
        )
    if message.get_content_type() == "text/html":
        return _decode_part(message)
    return _decode_part(message)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")
