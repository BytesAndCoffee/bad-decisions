#!/usr/bin/env python3
"""Root-owned, narrowly scoped activator for staged Bad Decisions wheels."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


RELEASE_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
WHEEL_RE = re.compile(r"^bad_decisions-([0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.]+)?)-py3-none-any\.whl$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ActivationError(RuntimeError):
    pass


def _plain_directory(path: Path) -> None:
    mode = path.lstat().st_mode
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
        raise ActivationError(f"unsafe directory: {path}")


def load_request(app_root: Path) -> tuple[dict[str, str], Path]:
    request = app_root / "activation" / "request.json"
    mode = request.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        raise ActivationError("activation request must be a regular file")
    try:
        value = json.loads(request.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActivationError("activation request is not valid JSON") from exc
    required = {"release_id", "wheel", "sha256", "version"}
    if not isinstance(value, dict) or set(value) != required or not all(isinstance(value[key], str) for key in required):
        raise ActivationError("activation request has unexpected fields")
    if not RELEASE_RE.fullmatch(value["release_id"]):
        raise ActivationError("invalid release id")
    wheel_match = WHEEL_RE.fullmatch(value["wheel"])
    if not wheel_match or wheel_match.group(1) != value["version"]:
        raise ActivationError("wheel name and requested version do not match")
    if not SHA256_RE.fullmatch(value["sha256"]):
        raise ActivationError("invalid wheel digest")
    incoming = app_root / "incoming" / value["release_id"]
    _plain_directory(incoming)
    wheel = incoming / value["wheel"]
    wheel_mode = wheel.lstat().st_mode
    if not stat.S_ISREG(wheel_mode) or stat.S_ISLNK(wheel_mode) or wheel.stat().st_nlink != 1:
        raise ActivationError("staged wheel must be one regular, unlinked file")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if digest != value["sha256"]:
        raise ActivationError("staged wheel digest does not match request")
    return value, wheel


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise ActivationError(f"command failed ({completed.returncode}): {command[0]}")


def _as_user(user: str, command: list[str]) -> None:
    _run(["/usr/sbin/runuser", "--user", user, "--", *command])


def _freeze_tree(root: Path, uid: int, gid: int) -> None:
    """Freeze a service-built tree without following attacker-controlled links."""
    for _directory, directories, files, directory_fd in os.fwalk(root, topdown=False, follow_symlinks=False):
        for name in files:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                os.chown(name, uid, gid, dir_fd=directory_fd, follow_symlinks=False)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ActivationError("release contains a non-file filesystem object")
            descriptor = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                os.fchown(descriptor, uid, gid)
                os.fchmod(descriptor, 0o550 if info.st_mode & 0o111 else 0o440)
            finally:
                os.close(descriptor)
        for name in directories:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                os.chown(name, uid, gid, dir_fd=directory_fd, follow_symlinks=False)
            elif not stat.S_ISDIR(info.st_mode):
                raise ActivationError("release contains an invalid directory entry")
        os.fchown(directory_fd, uid, gid)
        os.fchmod(directory_fd, 0o750)


def _atomic_symlink(target: Path, link: Path) -> None:
    temporary = link.with_name(f".{link.name}.activate-{os.getpid()}")
    temporary.symlink_to(target)
    os.replace(temporary, link)


def _healthy(url: str, attempts: int = 30) -> bool:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def _record_good(app_root: Path, release_id: str) -> None:
    path = app_root / "good-releases"
    existing = path.read_text(encoding="ascii").splitlines() if path.exists() else []
    valid = [item for item in existing if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[0-9a-f]{8})?", item) and item != release_id]
    temporary = path.with_suffix(".new")
    temporary.write_text("".join(f"{item}\n" for item in [*valid, release_id][-20:]), encoding="ascii")
    os.replace(temporary, path)


def _write_result(app_root: Path, group_gid: int, payload: dict[str, str]) -> None:
    target = app_root / "activation" / "result.json"
    temporary = target.with_name(f".result-{os.getpid()}.json")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chown(temporary, 0, group_gid)
    temporary.chmod(0o640)
    os.replace(temporary, target)


def activate(args: argparse.Namespace) -> str:
    import grp
    import pwd

    app_root = args.app_root.resolve()
    _plain_directory(app_root)
    for name in ("incoming", "activation", "releases"):
        _plain_directory(app_root / name)
    value, wheel = load_request(app_root)
    release_id = value["release_id"]
    release = app_root / "releases" / release_id
    if release.exists() or release.is_symlink():
        raise ActivationError("release already exists")
    service = pwd.getpwnam(args.service_user)
    deploy_gid = grp.getgrnam(args.deploy_group).gr_gid
    current = app_root / "current"
    previous = current.resolve(strict=True) if current.is_symlink() else None
    release.mkdir(mode=0o750)
    os.chown(release, service.pw_uid, service.pw_gid)
    switched = False
    try:
        _as_user(args.service_user, [args.python, "-m", "venv", str(release / ".venv")])
        _as_user(args.service_user, [str(release / ".venv/bin/pip"), "install", str(wheel)])
        installed = subprocess.run(
            ["/usr/sbin/runuser", "--user", args.service_user, "--", str(release / ".venv/bin/python"), "-c", "from bad_decisions import __version__; print(__version__)"],
            check=False, text=True, capture_output=True,
        )
        if installed.returncode or installed.stdout.strip() != value["version"]:
            raise ActivationError("installed release version does not match request")
        _freeze_tree(release, 0, service.pw_gid)
        _atomic_symlink(release, current)
        switched = True
        _run(["systemctl", "restart", args.service_name])
        if not _healthy(f"http://{args.bind_host}:{args.port}/healthz"):
            raise ActivationError("new release failed its health check")
        _record_good(app_root, release_id)
        _write_result(app_root, deploy_gid, {"release_id": release_id, "status": "ok", "version": value["version"]})
        return release_id
    except Exception:
        if switched and previous is not None:
            _atomic_symlink(previous, current)
            subprocess.run(["systemctl", "restart", args.service_name], check=False)
        if release.exists():
            shutil.rmtree(release)
        raise
    finally:
        request = app_root / "activation" / "request.json"
        request.unlink(missing_ok=True)
        shutil.rmtree(app_root / "incoming" / release_id, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--deploy-group", required=True)
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--python", default="/usr/bin/python3.12")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("release activator must run as root", file=sys.stderr)
        return 2
    request_id = "unknown"
    try:
        request_id = json.loads((args.app_root / "activation/request.json").read_text(encoding="utf-8")).get("release_id", "unknown")
    except Exception:
        pass
    try:
        release_id = activate(args)
    except Exception as exc:
        try:
            import grp
            _write_result(args.app_root, grp.getgrnam(args.deploy_group).gr_gid, {"release_id": request_id, "status": "error", "message": str(exc)})
        except Exception:
            pass
        (args.app_root / "activation/request.json").unlink(missing_ok=True)
        print(f"activation failed: {exc}", file=sys.stderr)
        return 1
    print(f"activated {release_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
