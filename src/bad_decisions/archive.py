"""Portable, offline CardDeck archive support."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import PackConfigurationError
from .models import ID_PATTERN, Pack

FORMAT = "carddeck"
FORMAT_VERSION = 1
CARDDECK_SUFFIX = ".carddeck"
REQUIRED_MEMBERS = frozenset({"manifest.json", "pack.json", "LICENSE.txt", "ATTRIBUTION.md"})
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 5 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100


class ArchiveManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: str
    format_version: int
    pack_id: str = Field(pattern=ID_PATTERN)
    pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _error(message: str) -> PackConfigurationError:
    return PackConfigurationError(f"carddeck: {message}")


def _format_for_path(path: Path) -> str:
    if path.suffix == CARDDECK_SUFFIX:
        return FORMAT
    raise _error("archive filename must end in .carddeck")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _pack_payload(pack: Pack) -> bytes:
    return _canonical_json(pack.model_dump(mode="json"))


def _read_archive(path: Path) -> tuple[ArchiveManifest, Pack, bytes, str, str]:
    expected_format = _format_for_path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) != len(REQUIRED_MEMBERS) or set(names) != REQUIRED_MEMBERS:
                raise _error("archive must contain exactly manifest.json, pack.json, LICENSE.txt, and ATTRIBUTION.md")
            total = 0
            for info in infos:
                if Path(info.filename).name != info.filename or info.is_dir():
                    raise _error(f"unsafe archive member: {info.filename!r}")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise _error(f"symbolic links are not allowed: {info.filename!r}")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise _error(f"archive member is too large: {info.filename!r}")
                if info.file_size and not info.compress_size:
                    raise _error(f"invalid compressed member: {info.filename!r}")
                if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                    raise _error(f"archive member compression ratio is too high: {info.filename!r}")
                total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise _error("archive is too large")
            members = {name: archive.read(name) for name in REQUIRED_MEMBERS}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise _error(f"cannot read archive: {exc}") from exc

    try:
        manifest = ArchiveManifest.model_validate_json(members["manifest.json"])
    except ValidationError as exc:
        raise _error(f"invalid manifest: {exc}") from exc
    if manifest.format != expected_format or manifest.format_version != FORMAT_VERSION:
        raise _error("unsupported archive format or version")
    payload = members["pack.json"]
    if hashlib.sha256(payload).hexdigest() != manifest.pack_sha256:
        raise _error("pack.json checksum does not match manifest")
    try:
        pack = Pack.model_validate_json(payload)
    except ValidationError as exc:
        raise _error(f"invalid pack.json: {exc}") from exc
    if pack.metadata.id != manifest.pack_id:
        raise _error("manifest pack_id does not match pack metadata")
    try:
        license_text = members["LICENSE.txt"].decode("utf-8").strip()
        attribution = members["ATTRIBUTION.md"].decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise _error("license and attribution files must be UTF-8") from exc
    if not license_text or not attribution:
        raise _error("license and attribution files must not be empty")
    return manifest, pack, payload, license_text, attribution


def validate_archive(path: str | Path) -> Pack:
    """Validate an archive fully and return its pack without mutating disk."""

    _, pack, _, _, _ = _read_archive(Path(path))
    return pack


def export_pack(pack: Pack, destination: str | Path) -> Path:
    """Write a deterministic portable archive, refusing an existing target."""

    target = Path(destination)
    format_name = _format_for_path(target)
    if target.exists():
        raise _error(f"destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _pack_payload(pack)
    manifest = ArchiveManifest(
        format=format_name,
        format_version=FORMAT_VERSION,
        pack_id=pack.metadata.id,
        pack_sha256=hashlib.sha256(payload).hexdigest(),
    )
    contents = {
        "manifest.json": _canonical_json(manifest.model_dump()),
        "pack.json": payload,
        "LICENSE.txt": (pack.metadata.license_notice.strip() + "\n").encode("utf-8"),
        "ATTRIBUTION.md": (pack.metadata.attribution.strip() + "\n").encode("utf-8"),
    }
    try:
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(contents):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, contents[name])
    except (OSError, zipfile.BadZipFile) as exc:
        raise _error(f"cannot create archive: {exc}") from exc
    return target


def import_pack(archive_path: str | Path, registry_dir: str | Path) -> Path:
    """Atomically install an archive's JSON pack into an absolute registry directory."""

    _, pack, payload, _, _ = _read_archive(Path(archive_path))
    destination_dir = Path(registry_dir)
    if not destination_dir.is_absolute():
        raise _error("registry directory must be an absolute path")
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not destination_dir.is_dir():
        raise _error(f"registry path is not a directory: {destination_dir}")
    destination = destination_dir / f"{pack.metadata.id}.json"
    if destination.exists():
        raise _error(f"refusing to overwrite existing pack: {destination}")
    try:
        with tempfile.NamedTemporaryFile("wb", dir=destination_dir, prefix=".carddeck-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except UnboundLocalError:
            pass
        raise _error(f"cannot import pack: {exc}") from exc
    return destination


def initialize_registry(registry_dir: str | Path) -> tuple[Path, ...]:
    """Create an empty absolute registry directory populated with bundled packs."""

    destination_dir = Path(registry_dir)
    if not destination_dir.is_absolute():
        raise _error("registry directory must be an absolute path")
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not destination_dir.is_dir():
        raise _error(f"registry path is not a directory: {destination_dir}")
    if any(destination_dir.iterdir()):
        raise _error(f"refusing to initialize non-empty registry: {destination_dir}")
    source_dir = files("bad_decisions").joinpath("data/packs")
    copied: list[Path] = []
    try:
        for source in sorted(source_dir.iterdir(), key=lambda item: item.name):
            if source.name.endswith(".json"):
                target = destination_dir / source.name
                target.write_bytes(source.read_bytes())
                os.chmod(target, 0o644)
                copied.append(target)
    except OSError as exc:
        raise _error(f"cannot initialize registry: {exc}") from exc
    if not copied:
        raise _error("no bundled pack files found")
    return tuple(copied)
