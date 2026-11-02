"""Read-only GitHub Releases update discovery."""

from __future__ import annotations

import json
from urllib.error import HTTPError
import urllib.request
from dataclasses import dataclass


REPOSITORY = "Yalort/Diamond-In-The-Rough"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"
MAX_RELEASE_METADATA_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    notes: str


def version_key(value: str) -> tuple[int, ...]:
    """Return a comparable stable semantic version, accepting an optional v."""
    clean = value.strip().removeprefix("v")
    if not clean or any(not part.isdigit() for part in clean.split(".")):
        raise ValueError(f"Invalid release version: {value!r}")
    return tuple(int(part) for part in clean.split("."))


def _read_metadata(response) -> bytes:
    payload = response.read(MAX_RELEASE_METADATA_BYTES + 1)
    if len(payload) > MAX_RELEASE_METADATA_BYTES:
        raise ValueError("GitHub release metadata exceeds the allowed size")
    return payload


def fetch_latest_release(*, opener=urllib.request.urlopen, timeout: float = 10) -> ReleaseInfo | None:
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "CTP500PrinterApp-updater"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(_read_metadata(response).decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            # GitHub returns 404 for repositories that have no published releases.
            exc.close()
            return None
        raise
    if payload.get("draft") or payload.get("prerelease"):
        return None
    version = str(payload.get("tag_name", "")).removeprefix("v")
    version_key(version)
    return ReleaseInfo(version, str(payload.get("body", "")).strip())


def is_newer(release: ReleaseInfo, current_version: str) -> bool:
    return version_key(release.version) > version_key(current_version)
