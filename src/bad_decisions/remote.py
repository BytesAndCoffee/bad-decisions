"""Safe, explicit remote acquisition for public CardDeck archives."""

from __future__ import annotations

import contextlib
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .archive import CARDDECK_SUFFIX, MAX_ARCHIVE_BYTES, import_pack, validate_archive
from .errors import PackConfigurationError

MAX_INDEX_BYTES = 2 * 1024 * 1024


def _error(message: str) -> PackConfigurationError:
    return PackConfigurationError(f"remote carddeck: {message}")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _check_url(url: str, *, carddeck: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise _error("URL must be an absolute https URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise _error("URL must not contain credentials or a fragment")
    if carddeck and not parsed.path.endswith(CARDDECK_SUFFIX):
        raise _error("archive URL path must end in .carddeck")


def _read_url(url: str, *, limit: int, carddeck: bool = False) -> bytes:
    _check_url(url, carddeck=carddeck)
    request = Request(url, headers={"Accept": "application/json" if not carddeck else "application/zip"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > limit:
                raise _error(f"remote response exceeds {limit} byte limit")
            data = response.read(limit + 1)
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise _error("redirects are not allowed") from exc
        raise _error(f"remote server returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise _error(f"cannot download remote content: {exc}") from exc
    if len(data) > limit:
        raise _error(f"remote response exceeds {limit} byte limit")
    return data


def _download_archive(url: str, directory: Path, name: str, *, expected_id: str | None = None) -> Path:
    """Download and fully validate one archive into ``directory`` without touching a registry."""

    data = _read_url(url, limit=MAX_ARCHIVE_BYTES, carddeck=True)
    archive = directory / f"{name}{CARDDECK_SUFFIX}"
    archive.write_bytes(data)
    pack = validate_archive(archive)
    if expected_id is not None and pack.metadata.id != expected_id:
        raise _error(f"catalog pack ID {expected_id!r} does not match downloaded archive {pack.metadata.id!r}")
    return archive


def _import_download(url: str, registry_dir: str | Path, *, expected_id: str | None = None) -> Path:
    with tempfile.TemporaryDirectory(prefix="bad-decisions-carddeck-") as directory:
        return import_pack(_download_archive(url, Path(directory), "download", expected_id=expected_id), registry_dir)


def import_url(url: str, registry_dir: str | Path) -> Path:
    """Download, validate, and atomically import one HTTPS CardDeck archive."""

    return _import_download(url, registry_dir)


def _catalog_entries(payload: bytes) -> Iterable[tuple[str, str]]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(f"invalid catalog JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("packs"), list):
        raise _error("catalog must be a schema_version 1 object with a packs list")
    seen: set[str] = set()
    for item in document["packs"]:
        if not isinstance(item, dict):
            raise _error("catalog pack entry must be an object")
        archive = item.get("archive")
        url = item.get("url")
        pack_id = archive.get("pack_id") if isinstance(archive, dict) else None
        if not isinstance(pack_id, str) or not isinstance(url, str):
            raise _error("catalog pack entry must contain archive.pack_id and url strings")
        if pack_id in seen:
            raise _error(f"catalog contains duplicate pack ID: {pack_id}")
        seen.add(pack_id)
        _check_url(url, carddeck=True)
        yield pack_id, url


def import_index(url: str, registry_dir: str | Path, *, pack_ids: Iterable[str]) -> tuple[Path, ...]:
    """Import explicitly selected packs from a live HTTPS CardDeck catalog."""

    requested = tuple(pack_ids)
    if not requested:
        raise _error("select at least one pack ID")
    if len(requested) != len(set(requested)):
        raise _error("a pack ID may be selected only once")
    entries = dict(_catalog_entries(_read_url(url, limit=MAX_INDEX_BYTES)))
    missing = sorted(set(requested) - entries.keys())
    if missing:
        raise _error(f"catalog does not contain requested pack IDs: {', '.join(missing)}")
    # Download and validate every archive before installing any, then install all-or-nothing: if one
    # install fails, the packs this call already created are removed (import_pack never overwrites, so
    # every installed path is ours).  A process crash mid-install can still leave a partial set.
    with tempfile.TemporaryDirectory(prefix="bad-decisions-carddeck-") as directory:
        archives = [
            _download_archive(entries[pack_id], Path(directory), f"download-{number}", expected_id=pack_id)
            for number, pack_id in enumerate(requested)
        ]
        installed: list[Path] = []
        try:
            for archive in archives:
                installed.append(import_pack(archive, registry_dir))
        except BaseException:
            for path in installed:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
            raise
    return tuple(installed)
