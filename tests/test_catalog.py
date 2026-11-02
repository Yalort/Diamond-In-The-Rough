from __future__ import annotations

import unittest
from unittest.mock import patch

from ctp500_app import catalog
from ctp500_app.catalog import (
    CatalogClient,
    CatalogError,
    filter_items,
    manufacturer_names,
    manufacturer_options,
    parse_category_csv,
    spreadsheet_id_from_share_link,
)
from ctp500_app.config import CATEGORY_GIDS


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload)
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class CatalogTests(unittest.TestCase):
    def test_config_includes_six_categories_and_excludes_packing_sheet(self) -> None:
        self.assertEqual(
            [name for name, _gid in CATEGORY_GIDS],
            ["Weapons", "Gene Treatments", "Apparel", "Consumables/Gear", "Bionics", "Vehicles"],
        )
        self.assertNotIn("Packing Sheet", [name for name, _gid in CATEGORY_GIDS])

    def test_parse_filters_blank_names_and_preserves_values(self) -> None:
        payload = (
            "Manufacturer,Name,Type,Rank,Price\r\n"
            'Acme,"Widget, Mk 2",Tool,10,"1/10rnds"\r\n'
            ",,,,\r\n"
        ).encode()
        items = parse_category_csv("Consumables/Gear", 7, payload)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "Widget, Mk 2")
        self.assertEqual(items[0].kind, "Tool")
        self.assertEqual(items[0].price, "1/10rnds")
        self.assertEqual(items[0].source_row, 2)

    def test_empty_vehicle_sheet_is_valid(self) -> None:
        self.assertEqual(parse_category_csv("Vehicles", 8, b""), ())

    def test_missing_name_header_fails_validation(self) -> None:
        with self.assertRaises(CatalogError):
            parse_category_csv("Weapons", 0, b"Manufacturer,Price\nAcme,5\n")

    def test_kind_normalization_and_filtering(self) -> None:
        weapon = parse_category_csv(
            "Weapons",
            0,
            b"Manufacturer,Name,Element,Rank,Form Factor,Price\nAcme,Arc Gun,Arc,2,Pistol,25\n",
        )[0]
        apparel = parse_category_csv(
            "Apparel",
            1,
            b"Manufacturer,Name,Coverage,Rank,Price\nAcme,Cooling Vest,Chest,1,250\n",
        )[0]
        self.assertEqual(weapon.kind, "Pistol")
        self.assertEqual(apparel.kind, "Chest")
        self.assertEqual(filter_items([weapon, apparel], query="arc", kind="Pistol"), [weapon])
        self.assertEqual(filter_items([weapon, apparel], category="Apparel"), [apparel])

    def test_manufacturer_filter_splits_combined_values_and_is_inclusive(self) -> None:
        items = parse_category_csv(
            "Bionics",
            1,
            (
                "Manufacturer,Name,Type,Price\n"
                '"Acme, Brightcorp",Shared Eye,Optic,100\n'
                "Acme,Acme Eye,Optic,90\n"
                '"Brightcorp, Acme",Shared Arm,Limb,200\n'
                "All,Universal Tool,Tool,10\n"
            ).encode(),
        )

        self.assertEqual(manufacturer_names("Acme, Brightcorp, Acme"), ("Acme", "Brightcorp"))
        self.assertEqual(manufacturer_options(items), ["Acme", "Brightcorp"])
        self.assertEqual(
            [item.name for item in filter_items(items, manufacturer="Acme")],
            ["Shared Eye", "Acme Eye", "Shared Arm", "Universal Tool"],
        )
        self.assertEqual(
            [item.name for item in filter_items(items, manufacturer="Brightcorp")],
            ["Shared Eye", "Shared Arm", "Universal Tool"],
        )

    def test_fetch_is_all_or_error(self) -> None:
        calls = 0

        def opener(_request, timeout):
            nonlocal calls
            self.assertEqual(timeout, 3)
            calls += 1
            if calls == 2:
                raise OSError("offline")
            return FakeResponse(b"Manufacturer,Name\nAcme,Thing\n")

        client = CatalogClient(spreadsheet_id="a" * 20, categories=(("One", 1), ("Two", 2)), timeout=3, opener=opener)
        with self.assertRaisesRegex(CatalogError, "offline"):
            client.fetch()

    def test_google_sheets_share_links_are_accepted_without_bundling_a_dataset(self) -> None:
        sheet_id = "a" * 20
        self.assertEqual(spreadsheet_id_from_share_link(f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit?usp=sharing"), sheet_id)
        self.assertEqual(spreadsheet_id_from_share_link(f"https://drive.google.com/open?id={sheet_id}"), sheet_id)
        with self.assertRaisesRegex(CatalogError, "Google Sheets"):
            spreadsheet_id_from_share_link("https://drive.google.com/file/d/not-a-spreadsheet/view")

    def test_fetch_requires_a_saved_dataset(self) -> None:
        with self.assertRaisesRegex(CatalogError, "No dataset"):
            CatalogClient().fetch()

    def test_rejects_oversized_response_rows_and_fields(self) -> None:
        with patch.object(catalog, "MAX_CATEGORY_RESPONSE_BYTES", 8):
            client = CatalogClient(spreadsheet_id="a" * 20, categories=(("One", 1),), opener=lambda *_args, **_kwargs: FakeResponse(b"Name\nOversized\n"))
            with self.assertRaisesRegex(CatalogError, "download size"):
                client.fetch()

        with patch.object(catalog, "MAX_CATEGORY_ROWS", 1):
            with self.assertRaisesRegex(CatalogError, "too many rows"):
                parse_category_csv("Weapons", 0, b"Name\nOne\nTwo\n")

        with patch.object(catalog, "MAX_CATALOG_FIELD_CHARS", 3):
            with self.assertRaisesRegex(CatalogError, "allowed length"):
                parse_category_csv("Weapons", 0, b"Name\nLong\n")

    def test_rejects_too_many_items_across_categories(self) -> None:
        with patch.object(catalog, "MAX_CATALOG_ITEMS", 1):
            client = CatalogClient(
                spreadsheet_id="a" * 20,
                categories=(("One", 1), ("Two", 2)),
                opener=lambda *_args, **_kwargs: FakeResponse(b"Name\nItem\n"),
            )
            with self.assertRaisesRegex(CatalogError, "too many items"):
                client.fetch()

    def test_item_limit_rejects_before_consuming_the_rest_of_an_iterable(self) -> None:
        item = parse_category_csv("Weapons", 0, b"Name\nItem\n")[0]
        yielded = 0

        def items():
            nonlocal yielded
            for _index in range(100):
                yielded += 1
                yield item

        with patch.object(catalog, "MAX_CATALOG_ITEMS", 2):
            with self.assertRaisesRegex(CatalogError, "too many items"):
                catalog.validate_catalog_items(items())
        self.assertEqual(yielded, 3)


if __name__ == "__main__":
    unittest.main()
