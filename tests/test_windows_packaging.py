from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_windows_metadata", ROOT / "scripts" / "build_windows_metadata.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metadata = _module()


def test_windows_metadata_uses_one_immutable_checked_release_asset(tmp_path):
    archive = tmp_path / "regret-windows-x86_64-v2.1.0.zip"
    archive.write_bytes(b"a regrettable executable")
    output = tmp_path / "metadata"

    metadata.generate("2.1.0", archive, output)

    installer = (output / "winget" / "BytesAndCoffee.Regret.installer.yaml").read_text()
    chocolatey = (output / "chocolatey" / "tools" / "chocolateyinstall.ps1").read_text(
        encoding="utf-8-sig"
    )
    expected_url = (
        "https://github.com/BytesAndCoffee/bad-decisions/releases/download/"
        "v2.1.0/regret-windows-x86_64-v2.1.0.zip"
    )
    expected_hash = metadata.sha256(archive)
    for generated in (installer, chocolatey):
        assert expected_url in generated
        assert expected_hash in generated
    assert "NestedInstallerType: portable" in installer
    assert "PortableCommandAlias: regret" in installer

    expected_schemas = ("version", "defaultLocale", "installer")
    for name, schema in zip(
        ("BytesAndCoffee.Regret.yaml", "BytesAndCoffee.Regret.locale.en-US.yaml", "BytesAndCoffee.Regret.installer.yaml"),
        expected_schemas,
    ):
        text = (output / "winget" / name).read_text()
        assert text.startswith(f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.{schema}.1.10.0.schema.json\n")


@pytest.mark.parametrize("version", ["v2.1.0", "2.1", "2.1.0rc1", "2.1.0.0", ""])
def test_windows_metadata_rejects_non_release_versions(tmp_path, version):
    archive = tmp_path / "regret.zip"
    archive.write_bytes(b"regret")
    with pytest.raises(ValueError, match="X.Y.Z"):
        metadata.generate(version, archive, tmp_path / "out")


def test_windows_metadata_requires_the_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        metadata.generate("2.1.0", tmp_path / "missing.zip", tmp_path / "out")
