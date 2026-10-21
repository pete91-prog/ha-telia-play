"""Personal data must not appear in logs or exported diagnostics."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "telia_play"
_PACKAGE = "telia_play_privacy_pkg"
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


privacy = _load(f"{_PACKAGE}.privacy", "privacy.py")
_load(f"{_PACKAGE}.const", "const.py")
activation = _load(f"{_PACKAGE}.activation", "activation.py")
redact = privacy.redact
ActivationResult = activation.ActivationResult


class PrivacyTests(unittest.TestCase):
    def test_redacts_email(self) -> None:
        text = redact("Mailbox login failed for user@example.com")
        self.assertNotIn("user@example.com", text)
        self.assertIn("[redacted]", text)

    def test_redacts_activation_uuid(self) -> None:
        text = redact(
            "https://activate.teliacompany.com/startactivation/no/max/"
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )
        self.assertNotIn("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", text)

    def test_redacts_bearer_token(self) -> None:
        text = redact("authorization Bearer abcdefghijklmnopqrstuvwxyz012345")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", text)

    def test_activation_export_omits_links_and_codes(self) -> None:
        result = ActivationResult(
            item_id="1",
            name="Max",
            status="needs_code",
            message="Waiting",
            activation_url="https://www.max.com/activate/secret-link",
            pairing_code="847291",
            partner_url="https://www.max.com/activate/secret-link",
        )
        exported = result.as_dict()
        blob = str(exported)
        self.assertNotIn("secret-link", blob)
        self.assertNotIn("847291", blob)
        self.assertEqual(exported["status"], "needs_code")


if __name__ == "__main__":
    unittest.main()
