"""Tests for Telia Play activation-code bypass helpers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "telia_play"


def _load(name: str, file_name: str, extra_modules: dict | None = None):
    path = _ROOT / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    if extra_modules:
        sys.modules.update(extra_modules)
    spec.loader.exec_module(module)
    return module


# Models first (no HA imports), then activation which imports const + models? 
# activation.py imports .const — load const as a package-less module by faking the package.
_PACKAGE = "telia_play_pkg"
if _PACKAGE not in sys.modules:
    pkg = type(sys)("pkg")
    pkg.__path__ = [str(_ROOT)]
    pkg.__package__ = _PACKAGE
    sys.modules[_PACKAGE] = pkg

const = _load(f"{_PACKAGE}.const", "const.py")
models = _load(f"{_PACKAGE}.models", "models.py")
activation = _load(f"{_PACKAGE}.activation", "activation.py")

CatalogItem = models.CatalogItem
SelectionMenu = models.SelectionMenu
families_from_api = models.families_from_api
match_family = models.match_family
enrich_menu = models.enrich_menu
vas_activation_url = models.vas_activation_url
extract_activation_code = activation.extract_activation_code
infer_status = activation.infer_status
is_max_host = activation.is_max_host
is_telia_host = activation.is_telia_host
looks_like_url = activation.looks_like_url
parse_activation_input = activation.parse_activation_input
partner_hint = activation.partner_hint
with_partner_token = activation.with_partner_token
with_query = activation.with_query
wants_email = activation.wants_email
STATUS_CODE_READY = activation.STATUS_CODE_READY
STATUS_COMPLETED = activation.STATUS_COMPLETED
STATUS_NEEDS_CODE = activation.STATUS_NEEDS_CODE


def _item(item_id: str, name: str, *, selected: bool = True, url: str | None = None):
    return CatalogItem(
        id=item_id,
        name=name,
        points=50,
        vod=True,
        is_selected=selected,
        activation_url=url,
    )


class FamilyMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.families = families_from_api(
            [
                {
                    "id": "6",
                    "name": "Netflix",
                    "tvStreamingServiceIds": ["36"],
                    "needsActivation": True,
                    "vasHubProvisioning": True,
                },
                {
                    "id": "7",
                    "name": "Max",
                    "tvStreamingServiceIds": ["37"],
                    "needsActivation": True,
                    "vasHubProvisioning": True,
                },
                {
                    "id": "20",
                    "name": "HBO",
                    "tvStreamingServiceIds": ["74"],
                    "needsActivation": True,
                    "vasHubProvisioning": True,
                },
                {
                    "id": "21",
                    "name": "Viaplay",
                    "tvStreamingServiceIds": ["75"],
                    "needsActivation": True,
                    "vasHubProvisioning": False,
                },
            ],
            [
                {"id": "75", "familyId": "21", "storeIds": ["7166", "7056"]},
                {"id": "36", "familyId": "6", "storeIds": []},
            ],
        )

    def test_store_id_match(self) -> None:
        item = _item("7166", "Viaplay")
        family = match_family(item, self.families)
        self.assertIsNotNone(family)
        self.assertEqual(family.id, "21")
        self.assertFalse(family.vas_hub_provisioning)

    def test_hbo_max_is_max_not_hbo(self) -> None:
        item = _item("7198", "HBO Max standard")
        family = match_family(item, self.families)
        self.assertEqual(family.id, "7")

    def test_netflix_by_name(self) -> None:
        item = _item("7187", "Netflix")
        family = match_family(item, self.families)
        self.assertEqual(family.id, "6")
        self.assertTrue(family.vas_hub_provisioning)

    def test_enrich_marks_activation_without_email_url(self) -> None:
        menu = SelectionMenu(
            points_total=60,
            selectables=[_item("7187", "Netflix", url=None)],
        )
        enrich_menu(menu, self.families)
        item = menu.selectables[0]
        self.assertEqual(item.family_id, "6")
        self.assertTrue(item.needs_activation)

    def test_vas_url_fallback(self) -> None:
        item = _item("7187", "Netflix")
        item.family_name = "Netflix"
        url = vas_activation_url(
            {
                "services": [
                    {"name": "Viaplay", "activationUrl": "https://example/viaplay"},
                    {"name": "Netflix", "activationUrl": "https://example/netflix"},
                ]
            },
            item,
        )
        self.assertEqual(url, "https://example/netflix")


class ActivationHelperTests(unittest.TestCase):
    def test_extract_json_code(self) -> None:
        self.assertEqual(
            extract_activation_code('{"activationCode":"AB12CD"}'),
            "AB12CD",
        )

    def test_extract_norwegian_copy(self) -> None:
        html = "<p>Bruk denne koden: QZ9K2P på Viaplay.</p>"
        self.assertEqual(extract_activation_code(html), "QZ9K2P")

    def test_extract_ignores_noise(self) -> None:
        self.assertIsNone(extract_activation_code("<html><body>Netflix</body></html>"))

    def test_partner_token_keeps_existing_query(self) -> None:
        url = with_partner_token("https://account.teliaplay.no/activate?x=1", "tok")
        self.assertIn("x=1", url)
        self.assertIn("partnerToken=tok", url)
        self.assertIn("token=tok", url)

    def test_looks_like_url(self) -> None:
        self.assertTrue(looks_like_url("https://account.teliaplay.no/activate/abc"))
        self.assertFalse(looks_like_url("AB12CD"))

    def test_telia_host(self) -> None:
        self.assertTrue(is_telia_host("https://account.teliaplay.no/activate"))
        self.assertFalse(is_telia_host("https://www.netflix.com/tv"))

    def test_infer_code_ready(self) -> None:
        status = infer_status(
            final_url="https://account.teliaplay.no/activate",
            body='Bruk denne koden: 7H2K9L',
            http_status=200,
        )
        self.assertEqual(status, STATUS_CODE_READY)

    def test_infer_needs_code(self) -> None:
        status = infer_status(
            final_url="https://example/activate",
            body="Please enter the code from your email",
            http_status=200,
        )
        self.assertEqual(status, STATUS_NEEDS_CODE)

    def test_infer_completed(self) -> None:
        status = infer_status(
            final_url="https://account.teliaplay.no/activated",
            body="Tjenesten er aktivert",
            http_status=200,
        )
        self.assertEqual(status, STATUS_COMPLETED)

    def test_with_query_overwrite(self) -> None:
        url = with_query("https://example/a?code=old", code="new")
        self.assertIn("code=new", url)
        self.assertNotIn("code=old", url)

    def test_parse_max_sms_link(self) -> None:
        pasted = parse_activation_input(
            "Aktiver HBO Max: https://www.max.com/no/activate/abc123"
        )
        self.assertEqual(pasted.url, "https://www.max.com/no/activate/abc123")
        self.assertIsNone(pasted.code)
        self.assertEqual(partner_hint(pasted.url), "max")
        self.assertTrue(is_max_host(pasted.url))

    def test_parse_max_email_code(self) -> None:
        pasted = parse_activation_input("Your One-Time HBO Max Code: 847291")
        self.assertEqual(pasted.code, "847291")
        self.assertIsNone(pasted.url)

    def test_parse_bare_six_digit(self) -> None:
        pasted = parse_activation_input("847291")
        self.assertEqual(pasted.code, "847291")

    def test_max_page_wants_email(self) -> None:
        self.assertTrue(wants_email("Enter your email address to continue", None))
        self.assertEqual(
            infer_status(
                final_url="https://auth.max.com/login",
                body="Enter your email address",
                http_status=200,
            ),
            STATUS_NEEDS_CODE,
        )


if __name__ == "__main__":
    unittest.main()
