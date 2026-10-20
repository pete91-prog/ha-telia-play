"""Tests for Telia Play TV-points selection logic."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_MODELS = Path(__file__).resolve().parents[1] / "custom_components" / "telia_play" / "models.py"
_SPEC = importlib.util.spec_from_file_location("telia_play_models", _MODELS)
_MODELS_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = _MODELS_MOD
_SPEC.loader.exec_module(_MODELS_MOD)

CatalogItem = _MODELS_MOD.CatalogItem
NotEnoughPointsError = _MODELS_MOD.NotEnoughPointsError
SelectionMenu = _MODELS_MOD.SelectionMenu
UnknownServiceError = _MODELS_MOD.UnknownServiceError
ids_after_toggle = _MODELS_MOD.ids_after_toggle
ids_for_streaming_names = _MODELS_MOD.ids_for_streaming_names
match_item = _MODELS_MOD.match_item
menu_from_api = _MODELS_MOD.menu_from_api
normalize_username = _MODELS_MOD.normalize_username


def _item(
    item_id: str, name: str, points: int, *, vod: bool = True, selected: bool = False
) -> CatalogItem:
    return CatalogItem(
        id=item_id,
        name=name,
        points=points,
        vod=vod,
        is_selected=selected,
    )


def _menu(*items: CatalogItem, total: int = 60) -> SelectionMenu:
    return SelectionMenu(points_total=total, selectables=list(items))


class MatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            _item("7187", "Netflix", 50),
            _item("7198", "HBO Max standard", 30),
            _item("7166", "Viaplay", 45),
        ]

    def test_exact_name(self) -> None:
        self.assertEqual(match_item(self.items, "Netflix").id, "7187")

    def test_fuzzy_max(self) -> None:
        self.assertEqual(match_item(self.items, "max").id, "7198")

    def test_id(self) -> None:
        self.assertEqual(match_item(self.items, "7166").id, "7166")


class ToggleTests(unittest.TestCase):
    def test_enable_within_budget(self) -> None:
        menu = _menu(_item("1", "Netflix", 50), _item("2", "Max", 30), total=60)
        self.assertEqual(ids_after_toggle(menu, "2", True), ["2"])

    def test_enable_too_expensive(self) -> None:
        menu = _menu(
            _item("1", "Netflix", 50, selected=True),
            _item("2", "Max", 30),
            total=60,
        )
        with self.assertRaises(NotEnoughPointsError):
            ids_after_toggle(menu, "2", True)

    def test_disable_frees_points(self) -> None:
        menu = _menu(
            _item("1", "Netflix", 50, selected=True),
            _item("2", "Max", 30),
            total=60,
        )
        self.assertEqual(ids_after_toggle(menu, "1", False), [])

    def test_set_streaming_keeps_channels(self) -> None:
        menu = _menu(
            _item("1", "Netflix", 50),
            _item("2", "Max", 30),
            _item("9", "TV 2", 5, vod=False, selected=True),
            total=60,
        )
        ids = ids_for_streaming_names(menu, ["Max"])
        self.assertEqual(ids, ["2", "9"])

    def test_set_streaming_over_budget(self) -> None:
        menu = _menu(
            _item("1", "Netflix", 50),
            _item("2", "Viaplay", 45),
            total=60,
        )
        with self.assertRaises(NotEnoughPointsError):
            ids_for_streaming_names(menu, ["Netflix", "Viaplay"])

    def test_unknown_service(self) -> None:
        menu = _menu(_item("1", "Netflix", 50), total=60)
        with self.assertRaises(UnknownServiceError):
            menu.find("Disney+")


class UsernameTests(unittest.TestCase):
    def test_gmail_lowercased(self) -> None:
        self.assertEqual(
            normalize_username("User@Gmail.com"),
            "user@gmail.com",
        )

    def test_norwegian_mobile(self) -> None:
        self.assertEqual(normalize_username("999 99 999"), "99999999")
        self.assertEqual(normalize_username("+47 999 99 999"), "+4799999999")
        self.assertEqual(normalize_username("004799999999"), "+4799999999")


class ParseTests(unittest.TestCase):
    def test_menu_from_api(self) -> None:
        raw = {
            "pointsTotal": 60,
            "selectables": [
                {
                    "id": "7187",
                    "name": "Netflix",
                    "points": 50,
                    "vod": True,
                    "isSelected": True,
                    "logos": {"dark": {"url": "https://example/netflix.svg"}},
                    "externalRegistration": {"url": "https://example/activate"},
                },
                {
                    "id": "7044",
                    "name": "Viasat Explorer",
                    "points": 5,
                    "vod": False,
                    "isSelected": False,
                },
            ],
            "fixed": [{"id": "nrk", "name": "NRK", "points": 0, "vod": False}],
        }
        menu = menu_from_api(raw)
        self.assertEqual(menu.points_total, 60)
        self.assertEqual(menu.points_used, 50)
        self.assertEqual(menu.points_remaining, 10)
        self.assertEqual([service.name for service in menu.streaming_services], ["Netflix"])
        self.assertTrue(menu.streaming_services[0].needs_activation)
        self.assertEqual(menu.streaming_services[0].activation_url, "https://example/activate")
        self.assertEqual(len(menu.channels), 1)
        self.assertEqual(menu.fixed[0].name, "NRK")

    def test_needs_activation_from_family_flag(self) -> None:
        item = _item("7187", "Netflix", 50, selected=True)
        item.family_needs_activation = True
        self.assertTrue(item.needs_activation)
        item.is_selected = False
        self.assertFalse(item.needs_activation)


if __name__ == "__main__":
    unittest.main()
