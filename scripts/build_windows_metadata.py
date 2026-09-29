"""Generate WinGet and Chocolatey metadata for a released Regret ZIP."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

PACKAGE_ID = "BytesAndCoffee.Regret"
REPOSITORY = "https://github.com/BytesAndCoffee/bad-decisions"


def release_url(version: str) -> str:
    return f"{REPOSITORY}/releases/download/v{version}/regret-windows-x86_64-v{version}.zip"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def generate(version: str, archive: Path, output: Path) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("version must be X.Y.Z")
    if not archive.is_file():
        raise FileNotFoundError(archive)

    checksum = sha256(archive)
    url = release_url(version)
    winget = output / "winget"
    chocolatey = output / "chocolatey"
    tools = chocolatey / "tools"
    winget.mkdir(parents=True, exist_ok=True)
    tools.mkdir(parents=True, exist_ok=True)

    (winget / f"{PACKAGE_ID}.yaml").write_text(
        f"""PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: 1.10.0
""",
        encoding="utf-8",
    )
    (winget / f"{PACKAGE_ID}.locale.en-US.yaml").write_text(
        f"""PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
PackageLocale: en-US
Publisher: BytesAndCoffee
PackageName: Regret
License: MIT
LicenseUrl: {REPOSITORY}/blob/v{version}/LICENSE
ShortDescription: The Bad Decisions terminal client, including the Peer Pressure TUI.
PackageUrl: {REPOSITORY}
PublisherUrl: https://bytes.coffee/
Tags:
- cli
- game
- multiplayer
- terminal
ManifestType: defaultLocale
ManifestVersion: 1.10.0
""",
        encoding="utf-8",
    )
    (winget / f"{PACKAGE_ID}.installer.yaml").write_text(
        f"""PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
InstallerType: zip
NestedInstallerType: portable
NestedInstallerFiles:
- RelativeFilePath: regret.exe
  PortableCommandAlias: regret
Installers:
- Architecture: x64
  InstallerUrl: {url}
  InstallerSha256: {checksum}
ManifestType: installer
ManifestVersion: 1.10.0
""",
        encoding="utf-8",
    )

    (chocolatey / "regret.nuspec").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd">
  <metadata>
    <id>regret</id>
    <version>{version}</version>
    <title>Regret</title>
    <authors>BytesAndCoffee</authors>
    <projectUrl>{REPOSITORY}</projectUrl>
    <licenseUrl>{REPOSITORY}/blob/v{version}/LICENSE</licenseUrl>
    <requireLicenseAcceptance>false</requireLicenseAcceptance>
    <description>The Bad Decisions terminal client, including the Peer Pressure TUI.</description>
    <summary>A native Windows client for Bad Decisions.</summary>
    <tags>regret bad-decisions cli game multiplayer terminal</tags>
  </metadata>
  <files>
    <file src="tools\\**" target="tools" />
  </files>
</package>
""",
        encoding="utf-8",
    )
    (tools / "chocolateyinstall.ps1").write_text(
        f"""$ErrorActionPreference = 'Stop'

$packageArgs = @{{
  packageName    = 'regret'
  url64bit       = '{url}'
  unzipLocation = $toolsDir
  checksum64     = '{checksum}'
  checksumType64 = 'sha256'
}}

Install-ChocolateyZipPackage @packageArgs
""",
        encoding="utf-8-sig",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.version, args.archive, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
