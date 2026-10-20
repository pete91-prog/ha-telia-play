"""IMAP mailbox helper reads Max mail from a user-owned inbox."""

from __future__ import annotations

import imaplib
import importlib.util
import sys
import unittest
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "telia_play"
_PACKAGE = "telia_play_mail_pkg"
if _PACKAGE not in sys.modules:
    pkg = type(sys)("pkg")
    pkg.__path__ = [str(_ROOT)]
    pkg.__package__ = _PACKAGE
    sys.modules[_PACKAGE] = pkg


def _load(name: str, file_name: str):
    path = _ROOT / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load(f"{_PACKAGE}.const", "const.py")
mail = _load(f"{_PACKAGE}.mail", "mail.py")

MailError = mail.MailError
fetch_recent_messages = mail.fetch_recent_messages
probe_imap = mail.probe_imap


def _raw_message(sender: str, subject: str, body: str) -> bytes:
    message = MIMEText(body, "plain", "utf-8")
    message["From"] = sender
    message["Subject"] = subject
    return message.as_bytes()


class FakeIMAP:
    def __init__(self, *_args, **_kwargs) -> None:
        self.logged_in = False
        self.selected = None
        self.logged_out = False
        self.password = "app-password"
        self.messages = {
            "11": _raw_message(
                "HBO Max <noreply@email.max.com>",
                "Your Max verification code",
                "Your one-time code is 847291",
            )
        }

    def login(self, username: str, password: str) -> None:
        if password != self.password:
            raise imaplib.IMAP4.error(b"LOGIN failed")
        self.logged_in = True

    def select(self, folder: str, readonly: bool = False):
        self.selected = (folder, readonly)
        return "OK", [b"1"]

    def uid(self, command: str, *args):
        if command == "SEARCH":
            return "OK", [b"11"]
        if command == "FETCH":
            uid = str(args[0])
            raw = self.messages.get(uid)
            if raw is None:
                return "OK", [None]
            return "OK", [(b"11 (BODY[] {n}", raw, b")")]
        raise imaplib.IMAP4.error(b"unknown")

    def logout(self):
        self.logged_out = True
        return "BYE", []


class MailHelperTests(unittest.TestCase):
    def test_probe_requires_credentials(self) -> None:
        with self.assertRaises(MailError):
            probe_imap("imap.gmail.com", 993, "", "")

    def test_probe_rejects_bad_password(self) -> None:
        fake = FakeIMAP()

        def _factory(*args, **kwargs):
            return fake

        with patch.object(mail.imaplib, "IMAP4_SSL", _factory):
            with self.assertRaises(MailError):
                probe_imap("imap.gmail.com", 993, "me@example.com", "wrong")

    def test_fetch_max_message(self) -> None:
        fake = FakeIMAP()

        def _factory(*args, **kwargs):
            return fake

        with patch.object(mail.imaplib, "IMAP4_SSL", _factory):
            messages = fetch_recent_messages(
                "imap.gmail.com",
                993,
                "me@example.com",
                "app-password",
            )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].uid, "11")
        self.assertIn("max.com", messages[0].sender.lower())
        self.assertIn("847291", messages[0].body)
        self.assertTrue(fake.logged_out)
        self.assertEqual(fake.selected, ("INBOX", True))


if __name__ == "__main__":
    unittest.main()
