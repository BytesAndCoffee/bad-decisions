"""Linux-native operational commands for a Bad Decisions service."""

from __future__ import annotations

import argparse
import os
import platform
import secrets
import subprocess
import sys
from pathlib import Path

from .packs import load_registry
from .settings import Settings

DEFAULT_CONFIG = Path.home() / ".config" / "bad-decisions" / "config.env"
AWS_ENV = Path.home() / ".bad-decisions.env"


def _require_linux() -> None:
    if platform.system() != "Linux":
        raise RuntimeError("Bad Decisions deployment commands are supported on Linux only")


def _service(action: str, *, require_root: bool = False) -> int:
    _require_linux()
    command = ["systemctl", action, "bad-decisions.service"]
    if require_root and os.geteuid() != 0:
        print(f"bad-decisions {action} must be run with sudo.", file=sys.stderr)
        return 2
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


def setup_aws(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions setup aws", description="Create local AWS deployment credentials.")
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "bad-decisions"))
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "ca-west-1"))
    parser.add_argument("--secret-id", default="bad-decisions/container-auth")
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parents[2] / "infra" / "aws")
    args = parser.parse_args(argv)
    if not args.app_dir.is_dir(): parser.error(f"AWS CDK app directory not found: {args.app_dir}")
    key_dir = Path.home() / ".config" / "bad-decisions"; key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_key = key_dir / "aws-container-auth"; public_key = Path(f"{private_key}.pub")
    if private_key.exists() or public_key.exists(): parser.error(f"key already exists: {private_key}")
    if subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "bad-decisions-local", "-f", str(private_key)], check=False).returncode: return 1
    payload = key_dir / f".{secrets.token_hex(8)}.public"; payload.write_text(public_key.read_text(encoding="utf-8"), encoding="utf-8"); payload.chmod(0o600)
    try:
        result = subprocess.run(["aws", "secretsmanager", "create-secret", "--name", args.secret_id, "--description", "Bad Decisions container authentication public key", "--secret-string", f"file://{payload}", "--profile", args.profile, "--region", args.region], check=False)
    finally: payload.unlink(missing_ok=True)
    if result.returncode: return result.returncode
    AWS_ENV.write_text(f"AWS_PROFILE={args.profile}\nAWS_DEFAULT_REGION={args.region}\nBAD_DECISIONS_AWS_SECRET_ID={args.secret_id}\nBAD_DECISIONS_AWS_APP_DIR={args.app_dir}\nBAD_DECISIONS_AWS_PRIVATE_KEY={private_key}\n", encoding="utf-8"); AWS_ENV.chmod(0o600)
    print(f"AWS configuration ready: {AWS_ENV}"); print("The private key remains local; only its public key was stored in Secrets Manager."); return 0


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


def deploy_aws(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions deploy aws", description="Deploy the AWS hybrid compute plane with CDK.")
    parser.add_argument("--image", required=True, help="immutable ECR image URI"); parser.add_argument("--desired-count", type=int, default=2)
    args = parser.parse_args(argv)
    if args.desired_count < 1: parser.error("--desired-count must be positive")
    if not AWS_ENV.is_file(): print(f"Missing {AWS_ENV}; run bad-decisions setup aws first.", file=sys.stderr); return 2
    values = dict(line.split("=", 1) for line in AWS_ENV.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    app_dir = Path(values.get("BAD_DECISIONS_AWS_APP_DIR", ""))
    if not app_dir.is_dir(): print(f"AWS CDK app directory not found: {app_dir}", file=sys.stderr); return 2
    env = os.environ.copy(); env.update({k: values[k] for k in ("AWS_PROFILE", "AWS_DEFAULT_REGION") if k in values}); env.update({"BAD_DECISIONS_IMAGE": args.image, "BAD_DECISIONS_DESIRED_COUNT": str(args.desired_count), "BAD_DECISIONS_REPOSITORY_NAME": args.image.split("/")[-1].split(":", 1)[0]})
    return subprocess.run(["npx", "--yes", "aws-cdk", "deploy", "BadDecisionsHybrid", "--require-approval", "never"], cwd=app_dir, env=env, check=False).returncode


def run(argv: list[str]) -> int:
    if not argv:
        return 2
    command, rest = argv[0], argv[1:]
    if command == "setup":
        return setup_aws(rest[1:]) if rest and rest[0] == "aws" else setup(rest)
    if command == "serve":
        return serve(rest)
    if command == "deploy":
        return deploy_aws(rest[1:]) if rest and rest[0] == "aws" else deploy(rest)
    if command == "status":
        return _service("status")
    if command == "reload":
        return _service("reload", require_root=True)
    if command == "stop":
        return _service("stop", require_root=True)
    raise ValueError(f"unknown operational command: {command}")
