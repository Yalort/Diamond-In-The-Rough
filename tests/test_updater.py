from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ctp500_app import updater
from ctp500_app.updater import ReleaseInfo, fetch_latest_release, is_newer, version_key


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return json.dumps(self.payload).encode("utf-8")[:size]


class UpdaterTests(unittest.TestCase):
    def test_release_parser_returns_stable_release_metadata(self) -> None:
        payload = {
            "tag_name": "v2.1.0", "body": "Updates", "assets": [],
        }
        release = fetch_latest_release(opener=lambda _request, timeout: _Response(payload))
        self.assertEqual(release, ReleaseInfo("2.1.0", "Updates"))

    def test_versions_compare_as_semantic_numbers(self) -> None:
        release = ReleaseInfo("1.10.0", "")
        self.assertEqual(version_key("v1.2.3"), (1, 2, 3))
        self.assertTrue(is_newer(release, "1.9.9"))
        self.assertFalse(is_newer(release, "1.10.0"))

    def test_missing_latest_release_is_not_an_error(self) -> None:
        def missing(_request, timeout):
            raise HTTPError("https://example.com", 404, "Not Found", None, None)

        self.assertIsNone(fetch_latest_release(opener=missing))

    def test_release_metadata_is_bounded(self) -> None:
        with patch.object(updater, "MAX_RELEASE_METADATA_BYTES", 8):
            with self.assertRaisesRegex(ValueError, "allowed size"):
                fetch_latest_release(opener=lambda _request, timeout: _Response({"tag_name": "v2.1.0"}))

    def test_update_channel_is_the_replacement_repository(self) -> None:
        self.assertEqual(updater.REPOSITORY, "Yalort/Diamond-In-The-Rough")
