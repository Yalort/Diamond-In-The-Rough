from __future__ import annotations

import json
import os
import shutil
import sqlite3
import struct
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from .catalog_cache import MAX_CATALOG_CACHE_BYTES, snapshot_from_dict
from .config import app_data_dir


BACKUP_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATA_FILES = ("characters.db", "config.json", "catalog_cache.json")
MEMBER_SIZE_LIMITS = {
    MANIFEST_NAME: 64 * 1024,
    "config.json": 2 * 1024 * 1024,
    "catalog_cache.json": MAX_CATALOG_CACHE_BYTES,
    "characters.db": 256 * 1024 * 1024,
}
MAX_BACKUP_TOTAL_BYTES = 384 * 1024 * 1024
MAX_BACKUP_ARCHIVE_BYTES = 384 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4
MAX_COMPRESSION_RATIO = 200
COMPRESSION_RATIO_MIN_BYTES = 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class BackupError(RuntimeError):
    pass


def _preflight_archive(stream) -> None:
    stream.seek(0, os.SEEK_END)
    archive_size = stream.tell()
    if archive_size > MAX_BACKUP_ARCHIVE_BYTES:
        raise BackupError("Backup archive exceeds the compressed-size limit")
    tail_size = min(archive_size, 65_557)
    stream.seek(archive_size - tail_size)
    tail = stream.read(tail_size)
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or len(tail) - marker < 22:
        raise BackupError("Backup archive directory is missing")
    (
        _signature,
        disk_number,
        directory_disk,
        entries_on_disk,
        total_entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = struct.unpack_from("<4s4H2LH", tail, marker)
    if marker + 22 + comment_size != len(tail):
        raise BackupError("Backup archive directory is malformed")
    if disk_number or directory_disk or entries_on_disk != total_entries:
        raise BackupError("Multi-disk backup archives are not supported")
    if total_entries == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise BackupError("ZIP64 backup archives are not supported")
    if total_entries > MAX_ARCHIVE_MEMBERS or directory_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise BackupError("Backup archive contains too much directory metadata")
    if directory_offset + directory_size > archive_size - (len(tail) - marker):
        raise BackupError("Backup archive directory is inconsistent")
    stream.seek(0)


def _safe_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    allowed = {MANIFEST_NAME, *DATA_FILES}
    total_size = 0
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 1 or info.is_dir():
            raise BackupError(f"Unsafe archive member: {info.filename}")
        if info.filename not in allowed or info.filename in result:
            raise BackupError(f"Unexpected archive member: {info.filename}")
        if info.flag_bits & 0x1:
            raise BackupError(f"Encrypted archive member is not supported: {info.filename}")
        if info.compress_type not in ALLOWED_COMPRESSION:
            raise BackupError(f"Unsupported archive compression: {info.filename}")
        limit = MEMBER_SIZE_LIMITS[info.filename]
        if info.file_size < 0 or info.file_size > limit:
            raise BackupError(f"Archive member exceeds its size limit: {info.filename}")
        total_size += info.file_size
        if total_size > MAX_BACKUP_TOTAL_BYTES:
            raise BackupError("Backup exceeds the total size limit")
        if info.file_size >= COMPRESSION_RATIO_MIN_BYTES:
            if info.compress_size <= 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise BackupError(f"Archive member has an unsafe compression ratio: {info.filename}")
        result[info.filename] = info
    return result


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    chunks: list[bytes] = []
    total = 0
    with archive.open(info, "r") as source:
        while chunk := source.read(min(COPY_CHUNK_BYTES, MEMBER_SIZE_LIMITS[info.filename] - total + 1)):
            total += len(chunk)
            if total > MEMBER_SIZE_LIMITS[info.filename]:
                raise BackupError(f"Archive member exceeds its size limit: {info.filename}")
            chunks.append(chunk)
    if total != info.file_size:
        raise BackupError(f"Archive member size is inconsistent: {info.filename}")
    return b"".join(chunks)


def _copy_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path) -> None:
    total = 0
    with archive.open(info, "r") as source, destination.open("xb") as output:
        while chunk := source.read(COPY_CHUNK_BYTES):
            total += len(chunk)
            if total > MEMBER_SIZE_LIMITS[info.filename]:
                raise BackupError(f"Archive member exceeds its size limit: {info.filename}")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if total != info.file_size:
        raise BackupError(f"Archive member size is inconsistent: {info.filename}")


def _stage_and_validate(archive: zipfile.ZipFile, staging: Path) -> tuple[str, ...]:
    members = _safe_members(archive)
    if MANIFEST_NAME not in members:
        raise BackupError("Backup manifest is missing")
    manifest = json.loads(_read_member(archive, members[MANIFEST_NAME]).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != BACKUP_VERSION:
        raise BackupError("Unsupported backup version")
    declared = manifest.get("files")
    if not isinstance(declared, list) or not declared or any(name not in DATA_FILES for name in declared):
        raise BackupError("Backup manifest file list is invalid")
    if len(set(declared)) != len(declared):
        raise BackupError("Backup manifest contains duplicate files")
    if set(members) != {MANIFEST_NAME, *declared}:
        raise BackupError("Backup is missing a declared file")
    if "characters.db" not in declared:
        raise BackupError("Character database is missing")

    for name in declared:
        _copy_member(archive, members[name], staging / name)
    if "config.json" in declared:
        value = json.loads((staging / "config.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise BackupError("Configuration is not a JSON object")
    if "catalog_cache.json" in declared:
        snapshot_from_dict(json.loads((staging / "catalog_cache.json").read_text(encoding="utf-8")))
    connection = sqlite3.connect(staging / "characters.db")
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("Database integrity check failed")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "characters" not in tables:
            raise BackupError("Database does not contain character data")
    finally:
        connection.close()
    return tuple(declared)


def validate_backup(path: Path) -> tuple[str, ...]:
    try:
        with path.open("rb") as stream:
            _preflight_archive(stream)
            with zipfile.ZipFile(stream, "r") as archive, tempfile.TemporaryDirectory() as directory:
                return _stage_and_validate(archive, Path(directory))
    except BackupError:
        raise
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise BackupError(f"Invalid backup: {exc}") from exc


def export_backup(destination: Path, source_dir: Path | None = None) -> Path:
    source = source_dir or app_data_dir()
    files = [name for name in DATA_FILES if (source / name).is_file()]
    if "characters.db" not in files:
        raise BackupError("No character database is available to back up")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    manifest = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "files": files,
    }
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
            for name in files:
                archive.write(source / name, name)
        validate_backup(temporary)
        os.replace(temporary, destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def create_safety_backup(source_dir: Path | None = None) -> Path | None:
    source = source_dir or app_data_dir()
    if not (source / "characters.db").is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = source / "backups" / f"before-restore-{stamp}.zip"
    return export_backup(destination, source)


def restore_backup(archive_path: Path, destination_dir: Path | None = None) -> Path | None:
    destination = destination_dir or app_data_dir()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with archive_path.open("rb") as stream:
            _preflight_archive(stream)
            with zipfile.ZipFile(stream, "r") as archive, tempfile.TemporaryDirectory(dir=destination) as directory:
                staging = Path(directory)
                declared = _stage_and_validate(archive, staging)
                safety = create_safety_backup(destination)
                for name in declared:
                    target = destination / name
                    replacement = target.with_name(f".{target.name}.restore")
                    shutil.copy2(staging / name, replacement)
                    os.replace(replacement, target)
                return safety
    except BackupError:
        raise
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise BackupError(f"Invalid backup: {exc}") from exc
