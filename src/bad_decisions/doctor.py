"""Read-only health report for a running Bad Decisions deployment.

``bad-decisions doctor`` checks the HTTP surface a deploy must keep working and,
when APP_ROOT is present, the rootless activation inbox and free disk space.
It changes nothing, so it is safe to run at any time, before or after a deploy.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, message: str) -> None:
        print(f"ok    {message}")

    def warn(self, message: str) -> None:
        print(f"warn  {message}")

    def fail(self, message: str) -> None:
        print(f"FAIL  {message}")
        self.failures += 1


def _get(url: str, timeout: float, *, follow: bool = True) -> tuple[int, dict[str, str], bytes]:
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": f"bad-decisions-doctor/{__version__}"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(64 * 1024)


def _json(url: str, timeout: float) -> Any:
    status, _headers, body = _get(url, timeout)
    if status != 200:
        raise ValueError(f"HTTP {status}")
    return json.loads(body)


def check_service(report: Report, base: str, expected_version: str | None, timeout: float) -> None:
    try:
        health = _json(f"{base}/healthz", timeout)
    except (OSError, ValueError) as exc:
        report.fail(f"{base}/healthz did not answer: {exc}")
        return
    if not isinstance(health, dict) or health.get("status") != "ok":
        report.fail(f"/healthz reported {health}")
        return
    version = health.get("version")
    if expected_version is None or version == expected_version:
        report.ok(f"/healthz ok, version {version}, {health.get('pack_count')} packs")
    else:
        report.fail(f"/healthz reports version {version}, expected {expected_version}")
    try:
        packs = _json(f"{base}/v2/packs", timeout)
        if not isinstance(packs, list) or not packs:
            report.fail("/v2/packs lists no packs")
        elif health.get("pack_count") not in (None, len(packs)):
            report.fail(f"/v2/packs lists {len(packs)} packs but /healthz counts {health.get('pack_count')}")
        else:
            report.ok(f"/v2/packs lists {len(packs)} packs")
    except (OSError, ValueError) as exc:
        report.fail(f"/v2/packs failed: {exc}")
    try:
        round_ = _json(f"{base}/v2/round", timeout)
        if isinstance(round_, dict) and round_.get("result"):
            report.ok("/v2/round deals from every pack")
        else:
            report.fail("/v2/round returned no result")
    except (OSError, ValueError) as exc:
        report.fail(f"/v2/round failed: {exc}")
    try:
        status, _headers, body = _get(f"{base}/", timeout)
        if status == 200 and b"<html" in body.lower():
            report.ok("/ serves the web client")
        else:
            report.fail(f"/ returned HTTP {status}")
    except OSError as exc:
        report.fail(f"/ failed: {exc}")
    try:
        status, headers, _body = _get(f"{base}/docs/", timeout, follow=False)
        location = headers.get("location", headers.get("Location", ""))
        target = urllib.parse.urljoin(f"{base}/docs/", location) if location else ""
        if 300 <= status < 400 and not (target == base or target.startswith(f"{base}/")):
            report.fail(f"/docs/ redirects outside {base} (to {target})")
        elif status >= 400:
            report.fail(f"/docs/ returned HTTP {status}")
        else:
            report.ok("/docs/ stays within the public prefix")
    except OSError as exc:
        report.fail(f"/docs/ failed: {exc}")


def check_host(report: Report, app_root: Path, min_free_mb: int) -> None:
    activation, incoming = app_root / "activation", app_root / "incoming"
    if activation.is_dir():
        request = activation / "request.json"
        if request.exists() or request.is_symlink():
            report.fail(f"an activation request is still pending ({request})")
        else:
            report.ok("activation inbox is idle")
        try:
            leftovers = sorted(entry.name for entry in incoming.iterdir())
        except OSError:
            leftovers = []
        if leftovers:
            report.warn(f"{incoming} holds unused staged files: {', '.join(leftovers)}")
    free_mb = shutil.disk_usage(app_root).free // (1024 * 1024)
    if free_mb < min_free_mb:
        report.fail(f"only {free_mb} MiB free under {app_root}; the activator needs {min_free_mb} MiB for a release")
    else:
        report.ok(f"{free_mb} MiB free under {app_root}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bad-decisions doctor",
        description="Check a running deployment without changing it: the HTTP API, web client, and prefix, "
        "plus the activation inbox and free disk space when APP_ROOT exists.",
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="service base URL, including any public prefix (default: %(default)s)")
    parser.add_argument("--expect-version", default=__version__, help="version /healthz must report (default: this command's, %(default)s)")
    parser.add_argument("--any-version", action="store_true", help="accept whatever version is serving")
    parser.add_argument("--app-root", type=Path, default=Path("/opt/bad-decisions"))
    parser.add_argument("--min-free-mb", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    base = args.url.strip().rstrip("/")
    if urllib.parse.urlsplit(base).scheme not in {"http", "https"}:
        parser.error("--url must start with http:// or https://")
    if args.timeout <= 0 or args.min_free_mb < 0:
        parser.error("--timeout must be positive and --min-free-mb may not be negative")
    report = Report()
    check_service(report, base, None if args.any_version else args.expect_version, args.timeout)
    if args.app_root.is_dir():
        check_host(report, args.app_root, args.min_free_mb)
    if report.failures:
        print(f"{report.failures} check(s) failed", file=sys.stderr)
    return 1 if report.failures else 0
