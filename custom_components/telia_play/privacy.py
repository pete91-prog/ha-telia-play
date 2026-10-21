"""Keep personal data out of logs, diagnostics, and the public repo."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+")
_TOKEN_QUERY = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|refreshToken|partnerToken|token|password|code)=)[^&\s]+"
)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
_LONG_SECRET = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def redact(value: object) -> str:
    """Strip emails, tokens, and activation UUIDs from a log or error string."""
    text = str(value)
    text = _EMAIL.sub("[redacted]", text)
    text = _BEARER.sub(r"\1[redacted]", text)
    text = _TOKEN_QUERY.sub(r"\1[redacted]", text)
    text = _UUID.sub("[redacted]", text)
    text = _LONG_SECRET.sub("[redacted]", text)
    return text
