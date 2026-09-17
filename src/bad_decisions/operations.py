"""Linux-native operational commands for a Bad Decisions service."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

from .packs import load_registry
from .settings import Settings

DEFAULT_CONFIG = Path.home() / ".config" / "bad-decisions" / "config.env"


def _require_linux() -> None:
    if platform.system() != "Linux":
        raise RuntimeError("Bad Decisions deployment commands are supported on Linux only")


def _service(action: str) -> int:
    _require_linux()
    command = ["systemctl", action, "bad-decisions.service"]
    completed = subprocess.run(command, check=False)
    return completed.returncode


def setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions setup", description="Prepare user-local Bad Decisions configuration.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pack-dir", type=Path, help="absolute registry directory to record")
    args = parser.parse_args(argv)
    if args.pack_dir is not None:
        if not args.pack_dir.is_absolute() or not args.pack_dir.is_dir():
            parser.error("--pack-dir must name an existing absolute directory")
        load_registry(args.pack_dir)
    args.config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = ["BAD_DECISIONS_LOG_LEVEL=INFO"]
    if args.pack_dir is not None:
        lines.append(f"BAD_DECISIONS_PACK_DIR={args.pack_dir}")
    if not args.config.exists():
        args.config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        args.config.chmod(0o600)
    print(f"Configuration ready: {args.config}")
    print("Run sudo bad-decisions deploy after reviewing system configuration.")
    return 0


def serve(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions serve", description="Run Bad Decisions in the foreground.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.workers < 1:
        parser.error("--port must be 1..65535 and --workers must be positive")
    print(f"Loading pack registry...\nStarting {args.workers} worker(s)...\nNow serving Bad Decisions.\nListening on http://{args.host}:{args.port}")
    return subprocess.run([sys.executable, "-m", "uvicorn", "bad_decisions.api:create_app", "--factory", "--host", args.host, "--port", str(args.port), "--workers", str(args.workers)], check=False).returncode


def deploy(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions deploy", description="Deploy a source checkout using its portable Linux deployer.")
    parser.parse_args(argv)
    _require_linux()
    if os.geteuid() != 0:
        print("bad-decisions deploy must be run with sudo.", file=sys.stderr)
        return 2
    script = Path(__file__).resolve().parents[2] / "deploy.sh"
    if not script.is_file():
        print("Deployment assets are unavailable; use the source release deploy.sh.", file=sys.stderr)
        return 2
    return subprocess.run([str(script)], check=False).returncode


def run(argv: list[str]) -> int:
    if not argv:
        return 2
    command, rest = argv[0], argv[1:]
    if command == "setup":
        return setup(rest)
    if command == "serve":
        return serve(rest)
    if command == "deploy":
        return deploy(rest)
    if command == "status":
        return _service("status")
    if command == "reload":
        return _service("reload")
    if command == "stop":
        return _service("stop")
    raise ValueError(f"unknown operational command: {command}")
