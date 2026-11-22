"""Authenticated, stale-row-guarded writes to the configured Google Sheet.

The public CSV endpoint remains the catalog's read path.  This module is only
used after a user chooses to save an edit, at which point it obtains OAuth
credentials with the Google Sheets *write* scope.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import CatalogError, spreadsheet_id_from_share_link
from .config import app_data_dir
from .models import GearItem


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
OAUTH_CLIENT_FILENAME = "google_oauth_client.json"
OAUTH_TOKEN_FILENAME = "google_oauth_token.json"
MAX_OAUTH_CLIENT_BYTES = 128 * 1024
IMMUTABLE_ID_FIELDS = ("ID", "Item ID", "ItemID", "UUID")


class SheetsWriteError(RuntimeError):
    """Base exception for an attempted Google Sheets edit."""


class SheetsConfigurationError(SheetsWriteError):
    """The dataset URL or requested fields cannot be used for an edit."""


class SheetsAuthenticationError(SheetsWriteError):
    """The user needs to install or authorize the Google Sheets integration."""


class SheetsMetadataError(SheetsWriteError):
    """The catalog's stored tab gid is no longer present in the spreadsheet."""


class SheetsConflictError(SheetsWriteError):
    """The source row no longer matches the catalog item being edited."""


class SheetsUpdateError(SheetsWriteError):
    """Google could not read or save the requested edit."""


@dataclass(frozen=True, slots=True)
class SheetUpdateResult:
    """Details of a successful, narrow source-sheet update."""

    spreadsheet_id: str
    sheet_title: str
    source_row: int
    changed_fields: tuple[str, ...]


def oauth_client_path() -> Path:
    """Return the local Desktop OAuth client file location (never the repo)."""
    return app_data_dir() / OAUTH_CLIENT_FILENAME


def oauth_token_path() -> Path:
    """Return the local OAuth token cache location (never the repo)."""
    return app_data_dir() / OAUTH_TOKEN_FILENAME


def _atomic_write(path: Path, payload: bytes) -> None:
    """Replace a credential file without leaving a partial JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=path.name + ".", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def install_oauth_client(source: Path, destination: Path | None = None) -> Path:
    """Validate and locally install a Google Desktop OAuth client document."""
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise SheetsAuthenticationError(f"Could not read the OAuth client file: {exc}") from exc
    if len(payload) > MAX_OAUTH_CLIENT_BYTES:
        raise SheetsAuthenticationError("The OAuth client file is unexpectedly large.")
    try:
        document = json.loads(payload)
        installed = document["installed"]
        required = ("client_id", "client_secret", "auth_uri", "token_uri")
        if not isinstance(installed, dict) or any(not installed.get(key) for key in required):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SheetsAuthenticationError(
            "Choose a valid Google OAuth Desktop app client JSON file."
        ) from exc
    target = destination or oauth_client_path()
    _atomic_write(target, payload)
    return target


def _cell_column(index: int) -> str:
    """Convert a zero-based field index to an A1 column name."""
    if index < 0:
        raise ValueError("Column index cannot be negative")
    result = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _sheet_range(title: str, cell_range: str) -> str:
    # A sheet name is always single-quoted in A1 notation; embedded quotes are
    # doubled.  This prevents names from escaping the intended range.
    return "'" + title.replace("'", "''") + "'!" + cell_range


def _string_cell(value: object) -> str:
    return "" if value is None else str(value)


class GoogleSheetsWriteBack:
    """Save selected ``GearItem`` fields through the Google Sheets API.

    ``service_factory`` is intentionally injectable so the write algorithm can
    be tested without a network connection or installed Google client package.
    If omitted, authentication happens only when :meth:`update_item` is called.
    """

    def __init__(
        self,
        dataset_url: str,
        *,
        immutable_id_field: str | None = None,
        client_secrets_path: Path | None = None,
        token_path: Path | None = None,
        service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.dataset_url = dataset_url.strip()
        # ``None`` auto-detects conventional ID headers. An explicit empty
        # string disables lookup and uses conflict-checked source row numbers.
        self.immutable_id_field = (
            None if immutable_id_field is None else immutable_id_field.strip()
        )
        self.client_secrets_path = client_secrets_path or oauth_client_path()
        self.token_path = token_path or oauth_token_path()
        self._service_factory = service_factory

    def is_authorized(self) -> bool:
        """Return whether a reusable OAuth token is locally configured."""
        if not self.token_path.is_file():
            return False
        try:
            document = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(document, dict) and bool(
            document.get("refresh_token") or document.get("token")
        )

    def client_configured(self) -> bool:
        """Return whether this computer already has a Desktop OAuth client."""
        return self.client_secrets_path.is_file()

    def authorize(
        self,
        client_secrets_path: str | Path | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Install an optional client file and complete browser OAuth now."""
        if client_secrets_path is not None:
            install_oauth_client(Path(client_secrets_path), self.client_secrets_path)
            force = True
        if force or (self.token_path.exists() and not self.is_authorized()):
            # A new client/account or invalid token must not reuse the prior
            # authorization. The browser flow can recreate this local cache.
            try:
                self.token_path.unlink(missing_ok=True)
            except OSError as exc:
                raise SheetsAuthenticationError(
                    f"Could not replace the saved Google authorization: {exc}"
                ) from exc
        if self._service_factory is not None:
            self._service_factory()
        else:
            self._authenticated_service()

    def update_item(
        self,
        item: GearItem,
        changes: Mapping[str, object],
    ) -> SheetUpdateResult:
        """Write just ``changes`` after checking that the source item is current.

        A configured immutable ID finds a moved row.  Regardless of that
        setting, every original field is compared before any write occurs, so a
        spreadsheet edit observed by the final read produces a
        refresh-and-retry conflict.
        """
        immutable_id_field = self._effective_immutable_id_field(item)
        normalized_changes = self._validate_changes(item, changes, immutable_id_field)
        spreadsheet_id = self._spreadsheet_id()
        service = self._service_factory() if self._service_factory else self._authenticated_service()
        title = self._sheet_title(service, spreadsheet_id, item.source_gid)
        headers = self._headers(service, spreadsheet_id, title)
        header_columns = self._header_columns(headers)
        self._validate_item_fields(item, header_columns)

        row = item.source_row
        if immutable_id_field:
            row = self._row_for_immutable_id(
                service, spreadsheet_id, title, header_columns, item, immutable_id_field
            )
        if row < 2:
            raise SheetsConflictError("The catalog item does not point to a data row. Refresh the catalog and retry.")

        actual_values = self._row_values(service, spreadsheet_id, title, row)
        self._ensure_unchanged(item, header_columns, actual_values)
        self._write_changes(
            service, spreadsheet_id, title, row, header_columns, normalized_changes
        )
        return SheetUpdateResult(
            spreadsheet_id=spreadsheet_id,
            sheet_title=title,
            source_row=row,
            changed_fields=tuple(normalized_changes),
        )

    def _spreadsheet_id(self) -> str:
        if not self.dataset_url:
            raise SheetsConfigurationError("Configure a Google Sheets share URL before editing an entry.")
        try:
            return spreadsheet_id_from_share_link(self.dataset_url)
        except CatalogError as exc:
            raise SheetsConfigurationError(str(exc)) from exc

    def _effective_immutable_id_field(self, item: GearItem) -> str:
        if self.immutable_id_field is not None:
            return self.immutable_id_field
        return next(
            (field for field in IMMUTABLE_ID_FIELDS if item.field_map.get(field)), ""
        )

    @staticmethod
    def _validate_changes(
        item: GearItem,
        changes: Mapping[str, object],
        immutable_id_field: str = "",
    ) -> dict[str, str]:
        if not changes:
            raise SheetsConfigurationError("Choose at least one field to save.")
        available = {field for field, _value in item.fields}
        result: dict[str, str] = {}
        for field, value in changes.items():
            if not isinstance(field, str) or field not in available:
                raise SheetsConfigurationError(f"'{field}' is not a field in the selected catalog entry.")
            if field == immutable_id_field:
                raise SheetsConfigurationError(
                    f"'{field}' is the entry's immutable identity and cannot be edited."
                )
            text = _string_cell(value)
            if len(text) > 8_192:
                raise SheetsConfigurationError(f"'{field}' exceeds the catalog field length limit.")
            if field == "Name" and not text.strip():
                raise SheetsConfigurationError("'Name' cannot be blank because unnamed rows are not catalog entries.")
            result[field] = text
        return result

    def _authenticated_service(self) -> Any:
        """Build a Sheets client, starting Desktop OAuth only for a real save."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise SheetsAuthenticationError(
                "Google Sheets editing support is not installed. Install the application's required dependencies."
            ) from exc

        credentials = None
        if self.token_path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(str(self.token_path), [SHEETS_SCOPE])
            except (OSError, ValueError) as exc:
                raise SheetsAuthenticationError(
                    f"The saved Google sign-in token at {self.token_path} is invalid. Delete it and sign in again."
                ) from exc
        try:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            if not credentials or not credentials.valid:
                if not self.client_secrets_path.is_file():
                    raise SheetsAuthenticationError(
                        "Google Sheets editing needs a Desktop OAuth client file at "
                        f"{self.client_secrets_path}. Download one from Google Cloud and place it there."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets_path), [SHEETS_SCOPE])
                credentials = flow.run_local_server(port=0)
            _atomic_write(self.token_path, credentials.to_json().encode("utf-8"))
            return build("sheets", "v4", credentials=credentials, cache_discovery=False)
        except SheetsAuthenticationError:
            raise
        except Exception as exc:
            raise SheetsAuthenticationError(
                "Could not sign in to Google Sheets. Check the network and OAuth client configuration, then retry."
            ) from exc

    @staticmethod
    def _api_call(action: str, callback: Callable[[], Any]) -> Any:
        try:
            return callback()
        except SheetsWriteError:
            raise
        except Exception as exc:
            raise SheetsUpdateError(
                f"Could not {action}. Verify the signed-in account has Editor access and retry: {exc}"
            ) from exc

    def _sheet_title(self, service: Any, spreadsheet_id: str, gid: int) -> str:
        metadata = self._api_call(
            "read spreadsheet tabs",
            lambda: service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets(properties(sheetId,title))",
            ).execute(),
        )
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            if properties.get("sheetId") == gid:
                title = properties.get("title")
                if isinstance(title, str) and title:
                    return title
        raise SheetsMetadataError(
            f"The source tab for gid {gid} no longer exists. Refresh the catalog and retry."
        )

    def _headers(self, service: Any, spreadsheet_id: str, title: str) -> list[str]:
        values = self._values_get(service, spreadsheet_id, _sheet_range(title, "1:1"))
        rows = values.get("values", [])
        if not rows or not isinstance(rows[0], list):
            raise SheetsMetadataError(f"The '{title}' tab has no header row.")
        headers = [_string_cell(value).strip() for value in rows[0]]
        if not headers:
            raise SheetsMetadataError(f"The '{title}' tab has no header row.")
        return headers

    @staticmethod
    def _header_columns(headers: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            if header in result:
                raise SheetsMetadataError(
                    f"The source tab has duplicate '{header}' column headers, so it cannot be edited safely."
                )
            result[header] = index
        return result

    @staticmethod
    def _validate_item_fields(item: GearItem, header_columns: Mapping[str, int]) -> None:
        missing = [field for field, _value in item.fields if field not in header_columns]
        if missing:
            raise SheetsConflictError(
                "The source tab's columns changed since this item was loaded "
                f"({', '.join(missing[:3])}). Refresh the catalog and retry."
            )

    def _row_for_immutable_id(
        self,
        service: Any,
        spreadsheet_id: str,
        title: str,
        header_columns: Mapping[str, int],
        item: GearItem,
        immutable_id_field: str,
    ) -> int:
        column = header_columns.get(immutable_id_field)
        if column is None:
            raise SheetsConfigurationError(
                f"The immutable ID field '{immutable_id_field}' is not in the source tab."
            )
        expected_id = item.field_map.get(immutable_id_field, "")
        if not expected_id:
            raise SheetsConfigurationError(
                f"The selected item has no value for immutable ID field '{immutable_id_field}'."
            )
        response = self._values_get(
            service, spreadsheet_id, _sheet_range(title, f"{_cell_column(column)}:{_cell_column(column)}")
        )
        matches = [
            row_number
            for row_number, values in enumerate(response.get("values", []), start=1)
            if isinstance(values, list) and values and _string_cell(values[0]).strip() == expected_id
        ]
        if not matches:
            raise SheetsConflictError(
                f"No row has immutable ID '{expected_id}'. Refresh the catalog and retry."
            )
        if len(matches) != 1:
            raise SheetsConflictError(
                f"Immutable ID '{expected_id}' appears more than once. Resolve the duplicate before editing."
            )
        if matches[0] == 1:
            raise SheetsConflictError(
                f"Immutable ID '{expected_id}' points to the header row. Refresh the catalog and retry."
            )
        return matches[0]

    def _row_values(self, service: Any, spreadsheet_id: str, title: str, row: int) -> list[str]:
        response = self._values_get(service, spreadsheet_id, _sheet_range(title, f"{row}:{row}"))
        rows = response.get("values", [])
        if not rows or not isinstance(rows[0], list):
            return []
        return [_string_cell(value) for value in rows[0]]

    def _values_get(self, service: Any, spreadsheet_id: str, cell_range: str) -> dict[str, Any]:
        return self._api_call(
            "read the source row",
            lambda: service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=cell_range
            ).execute(),
        )

    @staticmethod
    def _ensure_unchanged(
        item: GearItem, header_columns: Mapping[str, int], actual_values: list[str]
    ) -> None:
        for field, expected in item.fields:
            actual = actual_values[header_columns[field]] if header_columns[field] < len(actual_values) else ""
            if actual.strip() != expected:
                raise SheetsConflictError(
                    f"The source row changed in '{field}' since it was loaded. Refresh the catalog and retry."
                )

    def _write_changes(
        self,
        service: Any,
        spreadsheet_id: str,
        title: str,
        row: int,
        header_columns: Mapping[str, int],
        changes: Mapping[str, str],
    ) -> None:
        data = [
            {
                "range": _sheet_range(title, f"{_cell_column(header_columns[field])}{row}"),
                "values": [[value]],
            }
            for field, value in changes.items()
        ]
        self._api_call(
            "save the source row",
            lambda: service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            ).execute(),
        )
