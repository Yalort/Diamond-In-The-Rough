from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ctp500_app.models import GearItem
from ctp500_app import sheets_writeback
from ctp500_app.sheets_writeback import (
    GoogleSheetsWriteBack,
    SheetsAuthenticationError,
    SheetsConfigurationError,
    SheetsConflictError,
    SheetsMetadataError,
    SheetsUpdateError,
    install_oauth_client,
    oauth_client_path,
    oauth_token_path,
)


SPREADSHEET_ID = "abcdefghijklmnopqrstuv"


def _item(*, row: int = 2, fields: tuple[tuple[str, str], ...] | None = None) -> GearItem:
    fields = fields or (("Item ID", "gear-17"), ("Name", "Arc Gun"), ("Price", "25"))
    field_map = dict(fields)
    return GearItem(
        category="Weapons",
        source_gid=17,
        source_row=row,
        fields=fields,
        name=field_map["Name"],
        manufacturer="",
        kind="",
        rank="",
        price=field_map["Price"],
    )


class FakeRequest:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeValues:
    def __init__(self, reads: dict[str, object]) -> None:
        self.reads = reads
        self.get_calls: list[dict[str, object]] = []
        self.batch_updates: list[dict[str, object]] = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.reads[kwargs["range"]])

    def batchUpdate(self, **kwargs):
        self.batch_updates.append(kwargs)
        return FakeRequest({"totalUpdatedCells": len(kwargs["body"]["data"])})


class FakeSpreadsheets:
    def __init__(self, metadata: object, values: FakeValues) -> None:
        self.metadata = metadata
        self._values = values

    def get(self, **_kwargs):
        return FakeRequest(self.metadata)

    def values(self):
        return self._values


class FakeService:
    def __init__(self, metadata: object, reads: dict[str, object]) -> None:
        self.values_api = FakeValues(reads)
        self.spreadsheets_api = FakeSpreadsheets(metadata, self.values_api)

    def spreadsheets(self):
        return self.spreadsheets_api


def _metadata(title: str = "Weapons") -> dict[str, object]:
    return {"sheets": [{"properties": {"sheetId": 17, "title": title}}]}


class SheetsWriteBackTests(unittest.TestCase):
    def _writer(self, service: FakeService, **kwargs) -> GoogleSheetsWriteBack:
        return GoogleSheetsWriteBack(
            f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit",
            service_factory=lambda: service,
            **kwargs,
        )

    def test_uses_gid_metadata_and_updates_only_requested_cells(self) -> None:
        service = FakeService(
            _metadata("O'Brien Weapons"),
            {
                "'O''Brien Weapons'!1:1": {"values": [["Item ID", "Name", "Price"]]},
                "'O''Brien Weapons'!A:A": {"values": [["Item ID"], ["gear-17"]]},
                "'O''Brien Weapons'!2:2": {"values": [["gear-17", "Arc Gun", "25"]]},
            },
        )

        result = self._writer(service).update_item(_item(), {"Price": "30"})

        self.assertEqual(result.sheet_title, "O'Brien Weapons")
        self.assertEqual(result.source_row, 2)
        self.assertEqual(result.changed_fields, ("Price",))
        self.assertEqual(len(service.values_api.batch_updates), 1)
        request = service.values_api.batch_updates[0]
        self.assertEqual(request["spreadsheetId"], SPREADSHEET_ID)
        self.assertEqual(request["body"], {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": "'O''Brien Weapons'!C2", "values": [["30"]]}],
        })

    def test_immutable_id_finds_a_row_that_moved_since_catalog_refresh(self) -> None:
        service = FakeService(
            _metadata(),
            {
                "'Weapons'!1:1": {"values": [["Item ID", "Name", "Price"]]},
                "'Weapons'!A:A": {"values": [["Item ID"], ["other"], [], [" gear-17 "]]},
                "'Weapons'!4:4": {"values": [["gear-17", "Arc Gun", "25"]]},
            },
        )

        result = self._writer(service, immutable_id_field="Item ID").update_item(
            _item(row=2), {"Name": "Arc Gun Mk II"}
        )

        self.assertEqual(result.source_row, 4)
        self.assertEqual(
            service.values_api.batch_updates[0]["body"]["data"],
            [{"range": "'Weapons'!B4", "values": [["Arc Gun Mk II"]]}],
        )

    def test_fallback_detects_a_shifted_or_changed_row_before_writing(self) -> None:
        service = FakeService(
            _metadata(),
            {
                "'Weapons'!1:1": {"values": [["Item ID", "Name", "Price"]]},
                "'Weapons'!2:2": {"values": [["other", "Different Gun", "25"]]},
            },
        )

        with self.assertRaisesRegex(SheetsConflictError, "Item ID"):
            self._writer(service, immutable_id_field="").update_item(_item(), {"Price": "30"})

        self.assertEqual(service.values_api.batch_updates, [])

    def test_immutable_id_duplicate_is_rejected_before_writing(self) -> None:
        service = FakeService(
            _metadata(),
            {
                "'Weapons'!1:1": {"values": [["Item ID", "Name", "Price"]]},
                "'Weapons'!A:A": {"values": [["Item ID"], ["gear-17"], ["gear-17"]]},
            },
        )

        with self.assertRaisesRegex(SheetsConflictError, "more than once"):
            self._writer(service, immutable_id_field="Item ID").update_item(_item(), {"Price": "30"})

        self.assertEqual(service.values_api.batch_updates, [])

    def test_immutable_id_cannot_be_changed(self) -> None:
        service = FakeService(_metadata(), {})

        with self.assertRaisesRegex(SheetsConfigurationError, "immutable identity"):
            self._writer(service).update_item(_item(), {"Item ID": "replacement"})

        self.assertEqual(service.values_api.batch_updates, [])

    def test_name_cannot_be_cleared(self) -> None:
        service = FakeService(_metadata(), {})

        with self.assertRaisesRegex(SheetsConfigurationError, "cannot be blank"):
            self._writer(service).update_item(_item(), {"Name": "  "})

        self.assertEqual(service.values_api.batch_updates, [])

    def test_missing_gid_has_an_actionable_error(self) -> None:
        service = FakeService({"sheets": []}, {})

        with self.assertRaisesRegex(SheetsMetadataError, "gid 17"):
            self._writer(service).update_item(_item(), {"Price": "30"})

    def test_google_error_is_wrapped_with_editor_access_guidance(self) -> None:
        service = FakeService(RuntimeError("403 forbidden"), {})

        with self.assertRaisesRegex(SheetsUpdateError, "Editor access"):
            self._writer(service).update_item(_item(), {"Price": "30"})

    def test_oauth_paths_are_under_the_app_data_location_not_the_repository(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(sheets_writeback, "app_data_dir", return_value=root):
                writer = GoogleSheetsWriteBack(
                    "sheet-id-is-invalid-on-purpose", service_factory=lambda: None
                )
                self.assertEqual(oauth_client_path(), root / "google_oauth_client.json")
                self.assertEqual(oauth_token_path(), root / "google_oauth_token.json")
                self.assertEqual(writer.client_secrets_path.parent, root)
                self.assertEqual(writer.token_path.parent, root)

    def test_authorize_installs_valid_desktop_client_and_replaces_old_token(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "downloaded.json"
            client = root / "app-data" / "client.json"
            token = root / "app-data" / "token.json"
            source.write_text(
                '{"installed":{"client_id":"id","client_secret":"secret",'
                '"auth_uri":"https://example.test/auth","token_uri":"https://example.test/token"}}',
                encoding="utf-8",
            )
            token.parent.mkdir(parents=True)
            token.write_text('{"token":"old"}', encoding="utf-8")
            calls = []
            writer = GoogleSheetsWriteBack(
                f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit",
                client_secrets_path=client,
                token_path=token,
                service_factory=lambda: calls.append(True),
            )

            writer.authorize(source)

            self.assertEqual(client.read_bytes(), source.read_bytes())
            self.assertTrue(writer.client_configured())
            self.assertFalse(token.exists())
            self.assertEqual(calls, [True])

    def test_forced_browser_authorization_reuses_installed_client(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / "client.json"
            token = root / "token.json"
            client.write_text("{}", encoding="utf-8")
            token.write_text('{"token":"old"}', encoding="utf-8")
            calls = []
            writer = GoogleSheetsWriteBack(
                f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit",
                client_secrets_path=client,
                token_path=token,
                service_factory=lambda: calls.append(True),
            )

            writer.authorize(force=True)

            self.assertTrue(writer.client_configured())
            self.assertFalse(token.exists())
            self.assertEqual(calls, [True])

    def test_invalid_oauth_client_is_rejected_without_installing_it(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bad.json"
            target = root / "installed.json"
            source.write_text('{"web": {}}', encoding="utf-8")

            with self.assertRaisesRegex(SheetsAuthenticationError, "Desktop app"):
                install_oauth_client(source, target)

            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
