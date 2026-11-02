from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import warnings
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ctp500_app import backup
from ctp500_app.backup import BackupError, export_backup, restore_backup, validate_backup
from ctp500_app.catalog import parse_category_csv
from ctp500_app.catalog_cache import load_catalog_cache, save_catalog_cache
from ctp500_app import catalog_cache
from ctp500_app.character_store import CharacterStore
from ctp500_app.config import UserSettings, save_user_settings
from ctp500_app.models import CatalogSnapshot


class CatalogCacheTests(unittest.TestCase):
    def test_snapshot_round_trip_and_corrupt_cache_is_ignored(self) -> None:
        item = parse_category_csv("Apparel", 7, b"Manufacturer,Name,Price\nAcme,Vest,25\n")[0]
        snapshot = CatalogSnapshot((item,), datetime.now().astimezone(), "digest")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog_cache.json"
            save_catalog_cache(snapshot, path)
            self.assertEqual(load_catalog_cache(path), snapshot)
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(load_catalog_cache(path))

    def test_oversized_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog_cache.json"
            path.write_bytes(b"{}" * 10)
            with patch.object(catalog_cache, "MAX_CATALOG_CACHE_BYTES", 8):
                self.assertIsNone(load_catalog_cache(path))


class AtomicSettingsTests(unittest.TestCase):
    def test_failed_replace_preserves_previous_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"bluetooth_address":"old"}', encoding="utf-8")
            with patch("ctp500_app.config._config_path", return_value=path), patch(
                "ctp500_app.config.os.replace", side_effect=OSError("interrupted")
            ):
                with self.assertRaises(OSError):
                    save_user_settings(UserSettings(bluetooth_address="new"))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["bluetooth_address"], "old")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class BackupTests(unittest.TestCase):
    def test_export_validate_and_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as source_name, tempfile.TemporaryDirectory() as target_name:
            source, target = Path(source_name), Path(target_name)
            CharacterStore(source / "characters.db")
            (source / "config.json").write_text('{"bluetooth_address":"printer"}', encoding="utf-8")
            archive = export_backup(source / "backup.zip", source)
            self.assertEqual(validate_backup(archive), ("characters.db", "config.json"))
            restore_backup(archive, target)
            self.assertTrue((target / "characters.db").is_file())
            self.assertEqual(json.loads((target / "config.json").read_text(encoding="utf-8"))["bluetooth_address"], "printer")
            connection = sqlite3.connect(target / "characters.db")
            try:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                connection.close()

    def test_rejects_missing_declared_member_and_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.zip"
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"version": 1, "files": ["characters.db"]}))
            with self.assertRaises(BackupError):
                validate_backup(missing)

            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("../characters.db", b"bad")
            with self.assertRaisesRegex(BackupError, "Unsafe"):
                validate_backup(unsafe)

    def test_rejects_oversized_and_excessively_compressed_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CharacterStore(root / "characters.db")
            archive = export_backup(root / "backup.zip", root)
            limits = dict(backup.MEMBER_SIZE_LIMITS)
            limits["characters.db"] = 1
            with patch.object(backup, "MEMBER_SIZE_LIMITS", limits):
                with self.assertRaisesRegex(BackupError, "size limit"):
                    validate_backup(archive)

            with patch.object(backup, "COMPRESSION_RATIO_MIN_BYTES", 1), patch.object(
                backup, "MAX_COMPRESSION_RATIO", 1
            ):
                with self.assertRaisesRegex(BackupError, "compression ratio"):
                    validate_backup(archive)

    def test_validation_and_restore_stream_archive_members(self) -> None:
        with tempfile.TemporaryDirectory() as source_name, tempfile.TemporaryDirectory() as target_name:
            source, target = Path(source_name), Path(target_name)
            CharacterStore(source / "characters.db")
            archive = export_backup(source / "backup.zip", source)
            with patch.object(zipfile.ZipFile, "read", side_effect=AssertionError("unbounded read")):
                self.assertEqual(validate_backup(archive), ("characters.db",))
                restore_backup(archive, target)
            self.assertTrue((target / "characters.db").is_file())

    def test_rejects_excessive_central_directory_metadata_before_zipfile_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "metadata-bomb.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive, "w") as output:
                    for _index in range(backup.MAX_ARCHIVE_MEMBERS + 1):
                        output.writestr("manifest.json", b"{}")
            with patch.object(zipfile, "ZipFile", side_effect=AssertionError("ZipFile parsed metadata")):
                with self.assertRaisesRegex(BackupError, "directory metadata"):
                    validate_backup(archive)


if __name__ == "__main__":
    unittest.main()
