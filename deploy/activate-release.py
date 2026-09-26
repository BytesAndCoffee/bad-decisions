#!/usr/bin/env python3
"""Root-owned, narrowly scoped activator for staged Bad Decisions wheels.

Everything under ``incoming/`` and ``activation/`` is writable by the deployment
group, so every access there is descriptor-relative, refuses symlinks, FIFOs,
and hard links, and is size-capped. Root copies the verified wheel and lock into
a private directory before the service user installs them, so the bytes that
were hashed are the bytes that get installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


RELEASE_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
GOOD_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[0-9a-f]{8})?$")
WHEEL_RE = re.compile(r"^bad_decisions-([0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.]+)?)-py3-none-any\.whl$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCK_LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9.+!]+$")
LOCK_NAME = "requirements.lock"
REQUEST_FIELDS = {"release_id", "wheel", "sha256", "version", "requirements_sha256"}
ROLLBACK_FIELDS = {"action", "request_id", "target"}
MAX_REQUEST_BYTES = 4096
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_LOCK_BYTES = 256 * 1024
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
# The owner of trusted directories and of the frozen release. Tests substitute
# their own uid to exercise the activator without root.
ROOT_UID = 0


class ActivationError(RuntimeError):
    pass


def _terminate(signum, _frame):
    raise SystemExit(128 + signum)


def _open_directory(name: str | Path, dir_fd: int | None = None, *, group_writable: bool = False) -> int:
    """Open a root-owned directory without following a symlink at ``name``."""
    try:
        descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise ActivationError(f"unsafe or missing directory: {name}") from exc
    info = os.fstat(descriptor)
    forbidden = 0o002 if group_writable else 0o022
    if info.st_uid != ROOT_UID or info.st_mode & forbidden:
        os.close(descriptor)
        raise ActivationError(f"directory has unsafe ownership or permissions: {name}")
    return descriptor


def _read_regular(dir_fd: int, name: str, limit: int) -> bytes:
    """Read one plain, singly linked file of at most ``limit`` bytes."""
    try:
        descriptor = os.open(name, READ_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise ActivationError(f"{name} must be an existing regular file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ActivationError(f"{name} must be one regular, unlinked file")
        if info.st_size > limit:
            raise ActivationError(f"{name} is too large")
        chunks, total = [], 0
        while chunk := os.read(descriptor, min(1024 * 1024, limit + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ActivationError(f"{name} is too large")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def parse_request(raw: bytes, state: dict[str, str] | None = None) -> dict[str, str]:
    """Validate a request; record its id in ``state`` as soon as that alone is valid,
    so a request rejected later still gets a result its sender recognizes."""
    state = {} if state is None else state
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ActivationError("activation request is not valid JSON") from exc
    if isinstance(value, dict) and "action" in value:
        # {"action": "rollback", "request_id": ID, "target": RELEASE_ID or null}
        if value.get("action") != "rollback" or set(value) != ROLLBACK_FIELDS or not isinstance(value["request_id"], str):
            raise ActivationError("activation request has unexpected fields")
        if not RELEASE_RE.fullmatch(value["request_id"]):
            raise ActivationError("invalid request id")
        state["release_id"] = value["request_id"]
        if value["target"] is not None and not (isinstance(value["target"], str) and GOOD_RE.fullmatch(value["target"])):
            raise ActivationError("invalid rollback target")
        return value
    if isinstance(value, dict) and isinstance(value.get("release_id"), str) and RELEASE_RE.fullmatch(value["release_id"]):
        state["release_id"] = state["stage"] = value["release_id"]
    if not isinstance(value, dict) or set(value) != REQUEST_FIELDS or not all(isinstance(value[key], str) for key in REQUEST_FIELDS):
        raise ActivationError("activation request has unexpected fields")
    if not RELEASE_RE.fullmatch(value["release_id"]):
        raise ActivationError("invalid release id")
    wheel_match = WHEEL_RE.fullmatch(value["wheel"])
    if not wheel_match or wheel_match.group(1) != value["version"]:
        raise ActivationError("wheel name and requested version do not match")
    if not SHA256_RE.fullmatch(value["sha256"]) or not SHA256_RE.fullmatch(value["requirements_sha256"]):
        raise ActivationError("invalid digest")
    return value


def validate_lock(raw: bytes) -> None:
    """Accept only exact ``name==version`` pins: no options, URLs, paths, or includes."""
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise ActivationError("requirements lock must be ASCII") from exc
    pins = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not pins or not all(LOCK_LINE_RE.fullmatch(pin) for pin in pins):
        raise ActivationError("requirements lock may contain only name==version pins")


def read_request(app_root: Path, state: dict[str, str] | None = None) -> dict:
    """Read and validate the pending deploy or rollback request."""
    app_fd = _open_directory(app_root)
    try:
        activation_fd = _open_directory("activation", app_fd, group_writable=True)
    finally:
        os.close(app_fd)
    try:
        return parse_request(_read_regular(activation_fd, "request.json", MAX_REQUEST_BYTES), state)
    finally:
        os.close(activation_fd)


def load_request(app_root: Path) -> tuple[dict[str, str], bytes, bytes]:
    """Validate a pending deploy request and return it with the verified wheel and lock bytes."""
    value = read_request(app_root)
    if "action" in value:
        raise ActivationError("expected a deploy request")
    return value, *load_staged(app_root, value)


def load_staged(app_root: Path, value: dict[str, str]) -> tuple[bytes, bytes]:
    app_fd = _open_directory(app_root)
    try:
        incoming_fd = _open_directory("incoming", app_fd, group_writable=True)
    finally:
        os.close(app_fd)
    try:
        try:
            stage_fd = os.open(value["release_id"], DIRECTORY_FLAGS, dir_fd=incoming_fd)
        except OSError as exc:
            raise ActivationError("staged release must be a plain directory") from exc
        try:
            wheel = _read_regular(stage_fd, value["wheel"], MAX_WHEEL_BYTES)
            lock = _read_regular(stage_fd, LOCK_NAME, MAX_LOCK_BYTES)
        finally:
            os.close(stage_fd)
    finally:
        os.close(incoming_fd)
    if hashlib.sha256(wheel).hexdigest() != value["sha256"]:
        raise ActivationError("staged wheel digest does not match request")
    if hashlib.sha256(lock).hexdigest() != value["requirements_sha256"]:
        raise ActivationError("staged requirements digest does not match request")
    validate_lock(lock)
    return wheel, lock


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise ActivationError(f"command failed ({completed.returncode}): {' '.join(command)}")


def _as_user(user: str, command: list[str]) -> None:
    _run(["/usr/sbin/runuser", "--user", user, "--", *command])


def _installed_version(user: str, python: Path) -> str | None:
    completed = subprocess.run(
        ["/usr/sbin/runuser", "--user", user, "--", str(python), "-c", "from bad_decisions import __version__; print(__version__)"],
        check=False, text=True, capture_output=True,
    )
    return None if completed.returncode else completed.stdout.strip()


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
            descriptor = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise ActivationError("release contains a hard-linked or replaced file")
                os.fchown(descriptor, uid, gid)
                os.fchmod(descriptor, 0o550 if opened.st_mode & 0o111 else 0o440)
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


def _verify_frozen(root: Path, uid: int) -> None:
    """Reject entries the service user created while the freeze walk was running.

    Once every directory is root-owned and not group-writable, nothing new can
    appear, so this second pass sees the final tree.
    """
    for _directory, directories, files, directory_fd in os.fwalk(root, follow_symlinks=False):
        for name in [*directories, *files, "."]:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if info.st_uid != uid or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o7022):
                raise ActivationError("release changed while it was being frozen")


def _atomic_symlink(target: Path, link: Path) -> None:
    temporary = link.with_name(f".{link.name}.activate-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def _healthy(url: str, version: str | None, attempts: int = 30) -> bool:
    """Wait for /healthz to answer 200 (and report ``version`` when one is expected)."""
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200 and (version is None or json.loads(response.read(4096)).get("version") == version):
                    return True
        except (OSError, ValueError, AttributeError):
            pass
        time.sleep(1)
    return False


def _record_good(app_root: Path, release_id: str, previous: Path | None) -> None:
    """Append to the rollback watermark, seeding it from the serving release like deploy.sh."""
    path = app_root / "good-releases"
    if path.exists():
        existing = path.read_text(encoding="ascii").splitlines()
    else:
        existing = [previous.name] if previous is not None else []
    valid = [item for item in existing if GOOD_RE.fullmatch(item) and item != release_id]
    temporary = path.with_suffix(".new")
    temporary.write_text("".join(f"{item}\n" for item in [*valid, release_id][-20:]), encoding="ascii")
    os.replace(temporary, path)


def _write_result(app_root: Path, group_gid: int, payload: dict[str, str]) -> None:
    """Publish the result in the group-writable activation directory without following links."""
    app_fd = _open_directory(app_root)
    try:
        activation_fd = _open_directory("activation", app_fd, group_writable=True)
    finally:
        os.close(app_fd)
    try:
        temporary = f".result-{os.urandom(8).hex()}.json"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=activation_fd)
        try:
            os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
            os.fchown(descriptor, ROOT_UID, group_gid)
            os.fchmod(descriptor, 0o640)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, "result.json", src_dir_fd=activation_fd, dst_dir_fd=activation_fd)
    finally:
        os.close(activation_fd)


def _cleanup_request(app_root: Path, release_id: str | None) -> None:
    """Remove the request (so the path unit stops firing) and the staged files."""
    try:
        app_fd = _open_directory(app_root)
    except ActivationError:
        return
    try:
        for name, entry in (("activation", "request.json"), ("incoming", release_id)):
            if entry is None:
                continue
            try:
                directory_fd = _open_directory(name, app_fd, group_writable=True)
            except ActivationError:
                continue
            try:
                info = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    shutil.rmtree(entry, dir_fd=directory_fd)
                else:
                    os.unlink(entry, dir_fd=directory_fd)
            except OSError as exc:
                print(f"warning: could not remove {name}/{entry}: {exc}", file=sys.stderr)
            finally:
                os.close(directory_fd)
    finally:
        os.close(app_fd)


def _usable_release(releases: Path, release_id: str) -> bool:
    """Same test as rollback.sh: a plain release directory with an executable venv python."""
    if not GOOD_RE.fullmatch(release_id):
        return False
    try:
        if not stat.S_ISDIR((releases / release_id).lstat().st_mode):
            return False
    except OSError:
        return False
    return os.access(releases / release_id / ".venv/bin/python", os.X_OK)


def select_rollback_target(app_root: Path, current_id: str, explicit: str | None) -> str:
    """Choose the rollback target exactly as deploy/rollback.sh does."""
    releases = app_root / "releases"
    good = app_root / "good-releases"
    target = None
    if explicit is not None:
        if not _usable_release(releases, explicit):
            raise ActivationError(f"Release {explicit} is not a usable release under {releases}.")
        target = explicit
    elif good.is_file():
        # Watermark: the newest release that passed its health checks, other than the current one.
        for candidate in good.read_text(encoding="ascii", errors="replace").splitlines():
            if candidate != current_id and _usable_release(releases, candidate):
                target = candidate
        if target is None:
            raise ActivationError(f"No known-good release other than {current_id} in {good}. Name one explicitly: bad-decisions rollback local RELEASE_ID")
    else:
        print(f"warning: {good} does not exist (host deployed before it was kept);", file=sys.stderr)
        print(f"warning: using the newest usable release older than {current_id}, which is not verified good.", file=sys.stderr)
        for entry in sorted(os.listdir(releases)):
            if entry < current_id and _usable_release(releases, entry):
                target = entry
        if target is None:
            raise ActivationError(f"No earlier release than {current_id} to roll back to.")
    if target == current_id:
        raise ActivationError(f"Release {target} is already current.")
    return target


def _rollback_watermark(app_root: Path, target: str) -> None:
    """Keep valid IDs only, sorted, up to and including the target: everything newer is dropped."""
    path = app_root / "good-releases"
    existing = path.read_text(encoding="ascii", errors="replace").splitlines() if path.is_file() else []
    entries = sorted({item for item in existing if GOOD_RE.fullmatch(item)} | {target})
    temporary = path.with_suffix(".new")
    kept = [item for item in entries if item <= target][-20:]
    temporary.write_text("".join(f"{item}\n" for item in kept), encoding="ascii")
    os.replace(temporary, path)


def rollback(args: argparse.Namespace, value: dict, state: dict[str, str]) -> dict[str, str]:
    """Switch ``current`` back to a known-good release; restore the serving one on failure."""
    import grp

    app_root = args.app_root
    state["release_id"] = value["request_id"]
    current = app_root / "current"
    if not current.is_symlink():
        raise ActivationError(f"No current release at {current}; nothing to roll back.")
    current_dir = current.resolve(strict=True)
    current_id = current_dir.name
    target = select_rollback_target(app_root, current_id, value["target"])
    deploy_gid = grp.getgrnam(args.deploy_group).gr_gid
    print(f"Rolling back {args.service_name}: {current_id} -> {target}", file=sys.stderr)
    try:
        _atomic_symlink(app_root / "releases" / target, current)
        _run(["systemctl", "restart", args.service_name])
        if not _healthy(f"http://{args.bind_host}:{args.port}/healthz", None, args.health_attempts):
            raise ActivationError("health check failed")
    except BaseException as exc:
        print(f"Release {target} could not be activated or failed its health check; restoring {current_id}.", file=sys.stderr)
        try:
            _atomic_symlink(current_dir, current)
            _run(["systemctl", "restart", args.service_name])
        except (OSError, ActivationError) as restore_error:
            print(f"RESTORE FAILED: {args.service_name} may be on the wrong release; check {current}: {restore_error}", file=sys.stderr)
        raise ActivationError(f"rollback to {target} failed ({exc}); restored {current_id}") from exc
    try:
        _rollback_watermark(app_root, target)
    except OSError as exc:
        print(f"warning: could not update {app_root / 'good-releases'}: {exc}", file=sys.stderr)
    result = {"release_id": value["request_id"], "status": "ok", "action": "rollback", "target": target, "previous": current_id}
    _write_result(app_root, deploy_gid, result)
    return result


def activate(args: argparse.Namespace, state: dict[str, str], value: dict[str, str]) -> str:
    import grp
    import pwd

    app_root = args.app_root
    release_id = state["release_id"] = state["stage"] = value["release_id"]
    wheel_bytes, lock_bytes = load_staged(app_root, value)
    app_fd = _open_directory(app_root)
    try:
        releases_fd = _open_directory("releases", app_fd)
    finally:
        os.close(app_fd)
    service = pwd.getpwnam(args.service_user)
    deploy_gid = grp.getgrnam(args.deploy_group).gr_gid
    current = app_root / "current"
    previous = current.resolve(strict=True) if current.is_symlink() else None
    release = app_root / "releases" / release_id
    try:
        os.mkdir(release_id, 0o750, dir_fd=releases_fd)
    except FileExistsError as exc:
        raise ActivationError("release already exists") from exc
    finally:
        os.close(releases_fd)
    os.chown(release, service.pw_uid, service.pw_gid, follow_symlinks=False)
    switched = False
    private = Path(tempfile.mkdtemp(prefix="bad-decisions-activate-"))
    try:
        wheel = private / value["wheel"]
        lock = private / LOCK_NAME
        wheel.write_bytes(wheel_bytes)
        lock.write_bytes(lock_bytes)
        for path, mode in ((wheel, 0o444), (lock, 0o444), (private, 0o755)):
            path.chmod(mode)
        pip = [str(release / ".venv/bin/pip"), "install", "--no-cache-dir", "--disable-pip-version-check"]
        _as_user(args.service_user, [args.python, "-m", "venv", str(release / ".venv")])
        _as_user(args.service_user, [*pip, "--requirement", str(lock)])
        _as_user(args.service_user, [*pip, "--no-deps", str(wheel)])
        if _installed_version(args.service_user, release / ".venv/bin/python") != value["version"]:
            raise ActivationError("installed release version does not match request")
        _freeze_tree(release, ROOT_UID, service.pw_gid)
        _verify_frozen(release, ROOT_UID)
        _atomic_symlink(release, current)
        switched = True
        _run(["systemctl", "restart", args.service_name])
        if not _healthy(f"http://{args.bind_host}:{args.port}/healthz", value["version"], args.health_attempts):
            raise ActivationError("new release failed its health check")
        _record_good(app_root, release_id, previous)
        _write_result(app_root, deploy_gid, {"release_id": release_id, "status": "ok", "version": value["version"]})
        return release_id
    except BaseException:
        if switched:
            if previous is not None:
                _atomic_symlink(previous, current)
            else:
                current.unlink(missing_ok=True)
            try:
                _run(["systemctl", "restart", args.service_name])
            except ActivationError as restart_error:
                print(f"warning: rollback restart failed: {restart_error}", file=sys.stderr)
        shutil.rmtree(release, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(private, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--deploy-group", required=True)
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--python", default="/usr/bin/python3.12")
    parser.add_argument("--health-attempts", type=int, default=30)
    args = parser.parse_args(argv)
    if os.geteuid() != ROOT_UID:
        print("release activator must run as root", file=sys.stderr)
        return 2
    if not args.app_root.is_absolute():
        print("--app-root must be absolute", file=sys.stderr)
        return 2
    previous_handler = signal.signal(signal.SIGTERM, _terminate)
    state: dict[str, str] = {}
    try:
        value = read_request(args.app_root, state)
        if "action" in value:
            outcome = rollback(args, value, state)
            print(f"rolled back {outcome['previous']} -> {outcome['target']}")
            return 0
        release_id = activate(args, state, value)
    except BaseException as exc:
        message = str(exc) if isinstance(exc, ActivationError) else f"{type(exc).__name__}: {exc}"
        try:
            import grp
            _write_result(args.app_root, grp.getgrnam(args.deploy_group).gr_gid, {"release_id": state.get("release_id", "unknown"), "status": "error", "message": message})
        except Exception as result_error:
            print(f"warning: could not write activation result: {result_error}", file=sys.stderr)
        print(f"activation failed: {message}", file=sys.stderr)
        return 1
    finally:
        _cleanup_request(args.app_root, state.get("stage"))
        signal.signal(signal.SIGTERM, previous_handler)
    print(f"activated {release_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
