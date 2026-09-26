"""Linux-native operational commands for a Bad Decisions service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .packs import load_registry
from .settings import Settings
from . import __version__

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


def _run(command: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, cwd=cwd, check=False, text=True, capture_output=capture)


def _require_commands(parser: argparse.ArgumentParser, *names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        parser.error(f"required command(s) not found: {', '.join(missing)}")


def _write_aws_env(values: dict[str, str]) -> None:
    AWS_ENV.write_text("".join(f"{key}={value}\n" for key, value in sorted(values.items())), encoding="utf-8")
    AWS_ENV.chmod(0o600)


def _aws_env(profile: str, region: str) -> dict[str, str]:
    return {**os.environ, "AWS_PROFILE": profile, "AWS_DEFAULT_REGION": region}


# Keep the newest images so --image redeploys and rollbacks have something to use.
_ECR_LIFECYCLE = json.dumps({"rules": [{"rulePriority": 1, "description": "Keep the newest 10 images", "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 10}, "action": {"type": "expire"}}]})

_DOMAIN_KEYS = {
    "certificate_arn": "BAD_DECISIONS_CERTIFICATE_ARN",
    "domain_name": "BAD_DECISIONS_DOMAIN_NAME",
    "hosted_zone_id": "BAD_DECISIONS_HOSTED_ZONE_ID",
    "hosted_zone_name": "BAD_DECISIONS_HOSTED_ZONE_NAME",
}


def _add_domain_arguments(parser: argparse.ArgumentParser) -> None:
    for name in _DOMAIN_KEYS:
        parser.add_argument(f"--{name.replace('_', '-')}")
    parser.add_argument("--allow-http", action="store_true", help="skip the custom domain and use the generated API URL, for a disposable smoke test")


def _merge_domain_arguments(args: argparse.Namespace, values: dict[str, str]) -> None:
    """Save domain flags given on this run; omitted flags keep their saved values."""
    for name, key in _DOMAIN_KEYS.items():
        if getattr(args, name):
            values[key] = getattr(args, name)
    if args.allow_http:
        values["BAD_DECISIONS_ALLOW_HTTP"] = "1"


def _put_management_token(env: dict[str, str], secret_id: str, *, create: bool) -> str | None:
    """Write a fresh management token to Secrets Manager and return it, or None on failure."""
    token = secrets.token_urlsafe(48)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as payload:
        payload.write(token); payload.flush(); os.chmod(payload.name, 0o600)
        command = ["aws", "secretsmanager", "create-secret", "--name", secret_id] if create else ["aws", "secretsmanager", "put-secret-value", "--secret-id", secret_id]
        if _run(command + ["--secret-string", f"file://{payload.name}"], env=env).returncode:
            return None
    return token


def setup_aws(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions setup aws", description="Prepare an AWS account for Bad Decisions (one-time; safe to repeat).")
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "decisions"))
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "ca-west-1"))
    parser.add_argument("--repository", default="bad-decisions")
    parser.add_argument("--secret-id", default="bad-decisions/management-token")
    parser.add_argument("--bootstrap", action="store_true", help="run cdk bootstrap even if the CDKToolkit stack exists (needs IAM permissions)")
    _add_domain_arguments(parser)
    args = parser.parse_args(argv)
    if args.certificate_arn and not args.domain_name:
        parser.error("--certificate-arn requires --domain-name")
    _require_commands(parser, "aws", "npx")
    env = _aws_env(args.profile, args.region)
    identity = _run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], env=env, capture=True)
    if identity.returncode:
        print(identity.stderr.strip(), file=sys.stderr); return identity.returncode
    account = identity.stdout.strip()
    described = _run(["aws", "ecr", "describe-repositories", "--repository-names", args.repository], env=env, capture=True)
    if described.returncode:
        created = _run(["aws", "ecr", "create-repository", "--repository-name", args.repository, "--image-tag-mutability", "IMMUTABLE", "--image-scanning-configuration", "scanOnPush=true"], env=env)
        if created.returncode: return created.returncode
    lifecycle = _run(["aws", "ecr", "put-lifecycle-policy", "--repository-name", args.repository, "--lifecycle-policy-text", _ECR_LIFECYCLE], env=env, capture=True)
    if lifecycle.returncode:
        print(lifecycle.stderr.strip(), file=sys.stderr); return lifecycle.returncode

    values = _read_aws_env()
    # Never rotate an existing token here: running tasks would keep the old one. Use rotate-token aws.
    exists = _run(["aws", "secretsmanager", "describe-secret", "--secret-id", args.secret_id], env=env, capture=True)
    missing_token = False
    if exists.returncode:
        token = _put_management_token(env, args.secret_id, create=True)
        if token is None: return 1
        values["BAD_DECISIONS_AWS_MANAGEMENT_TOKEN"] = token
    elif values.get("BAD_DECISIONS_AWS_SECRET_ID") != args.secret_id or not values.get("BAD_DECISIONS_AWS_MANAGEMENT_TOKEN"):
        values.pop("BAD_DECISIONS_AWS_MANAGEMENT_TOKEN", None)
        missing_token = True

    # Bootstrapping creates IAM roles, so only repeat it on request: day-to-day deploy
    # identities (for example PowerUserAccess) cannot, and do not need to.
    bootstrapped = _run(["aws", "cloudformation", "describe-stacks", "--stack-name", "CDKToolkit"], env=env, capture=True).returncode == 0
    if args.bootstrap or not bootstrapped:
        bootstrap = _run(["npx", "--yes", "aws-cdk", "bootstrap", f"aws://{account}/{args.region}"], env=env)
        if bootstrap.returncode: return bootstrap.returncode
    values.update({"AWS_PROFILE": args.profile, "AWS_DEFAULT_REGION": args.region, "BAD_DECISIONS_AWS_SECRET_ID": args.secret_id, "BAD_DECISIONS_REPOSITORY_NAME": args.repository, "BAD_DECISIONS_AWS_ACCOUNT": account})
    _merge_domain_arguments(args, values)
    _write_aws_env(values)
    print(f"AWS configuration ready: {AWS_ENV}")
    if missing_token:
        print(f"Secret {args.secret_id} already exists but its token is not saved locally; run bad-decisions rotate-token aws.", file=sys.stderr)
    return 0


def _publish_image(env: dict[str, str], values: dict[str, str], source: Path | None) -> str | None:
    """Build the installed release (or a source checkout), push it to ECR, and return its URI."""
    account = values.get("BAD_DECISIONS_AWS_ACCOUNT")
    if not account:
        identity = _run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], env=env, capture=True)
        if identity.returncode:
            print(identity.stderr.strip(), file=sys.stderr); return None
        account = identity.stdout.strip()
    repository_uri = f"{account}.dkr.ecr.{values['AWS_DEFAULT_REGION']}.amazonaws.com/{values.get('BAD_DECISIONS_REPOSITORY_NAME', 'bad-decisions')}"
    image_uri = f"{repository_uri}:{__version__}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    login = subprocess.Popen(["aws", "ecr", "get-login-password"], env=env, stdout=subprocess.PIPE)
    docker_login = subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", repository_uri], stdin=login.stdout, check=False)
    if login.stdout: login.stdout.close()
    if login.wait() or docker_login.returncode: return None
    try:
        if source:
            build = _run(["docker", "build", "-t", image_uri, str(source.resolve())])
        else:
            with tempfile.TemporaryDirectory(prefix="bad-decisions-image-") as directory:
                dockerfile = Path(directory) / "Dockerfile"
                dockerfile.write_text(f"FROM python:3.12-slim\nRUN pip install --no-cache-dir bad-decisions=={__version__} && useradd --create-home --uid 10001 --shell /usr/sbin/nologin baddecisions\nUSER baddecisions\nEXPOSE 8000\nCMD [\"uvicorn\", \"bad_decisions.api:create_app\", \"--factory\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\", \"--proxy-headers\"]\n", encoding="utf-8")
                build = _run(["docker", "build", "-t", image_uri, directory])
        if build.returncode or _run(["docker", "push", image_uri]).returncode: return None
    finally:
        _run(["docker", "logout", repository_uri], capture=True)
    print(f"Published image: {image_uri}")
    return image_uri


def rotate_token_aws(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions rotate-token aws", description="Replace the management token and restart every ECS task so they all use it.")
    parser.parse_args(argv)
    values = _read_aws_env()
    secret_id = values.get("BAD_DECISIONS_AWS_SECRET_ID")
    if not secret_id or not values.get("AWS_PROFILE") or not values.get("AWS_DEFAULT_REGION"):
        print(f"Missing AWS settings in {AWS_ENV}; run bad-decisions setup aws first.", file=sys.stderr); return 2
    token = _put_management_token(_aws_env(values["AWS_PROFILE"], values["AWS_DEFAULT_REGION"]), secret_id, create=False)
    if token is None: return 1
    values["BAD_DECISIONS_AWS_MANAGEMENT_TOKEN"] = token
    _write_aws_env(values)
    cluster, service = values.get("BAD_DECISIONS_AWS_CLUSTER"), values.get("BAD_DECISIONS_AWS_SERVICE")
    if cluster and service:
        from .aws_archive import force_runtime_reload
        force_runtime_reload(cluster=cluster, service=service, profile=values["AWS_PROFILE"], region=values["AWS_DEFAULT_REGION"])
        print("Token rotated; ECS is replacing tasks. The old token works until the old tasks stop.")
    else:
        print("Token rotated; no deployed service to restart.")
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


PYPI_RELEASE_JSON = "https://pypi.org/pypi/bad-decisions/{version}/json"
MAX_WHEEL_BYTES = 64 * 1024 * 1024
ACTIVATION_UNIT = "bad-decisions-activate.service"


def _release_lock() -> Path | None:
    """The dependency lock for this exact version: packaged in the wheel, or the source checkout's."""
    for candidate in (Path(__file__).with_name("requirements.lock"), Path(__file__).resolve().parents[2] / "requirements.lock"):
        if candidate.is_file():
            return candidate
    return None


def _download_release_wheel(expected: str, destination: Path) -> Path:
    """Fetch this version's wheel from PyPI and check it against PyPI's published SHA-256."""
    url = PYPI_RELEASE_JSON.format(version=__version__)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            files = json.load(response).get("urls", [])
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"bad-decisions {__version__} is not published on PyPI (HTTP {exc.code}); build a wheel and pass --wheel") from exc
    except (urllib.error.URLError, ValueError) as exc:
        raise RuntimeError(f"cannot read PyPI release metadata: {exc}") from exc
    match = next((item for item in files if isinstance(item, dict) and item.get("filename") == expected), None)
    if not match or not str(match.get("url", "")).startswith("https://") or not match.get("digests", {}).get("sha256"):
        raise RuntimeError(f"PyPI has no {expected} for bad-decisions {__version__}; build a wheel and pass --wheel")
    wheel = destination / expected
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(match["url"], timeout=60) as response, wheel.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_WHEEL_BYTES:
                    raise RuntimeError(f"{expected} from PyPI is unexpectedly large")
                digest.update(chunk)
                output.write(chunk)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot download {expected} from PyPI: {exc}") from exc
    if digest.hexdigest() != match["digests"]["sha256"]:
        raise RuntimeError(f"{expected} from PyPI does not match its published SHA-256")
    return wheel


def _local_request_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)


def _local_inbox(parser: argparse.ArgumentParser, app_root: Path, timeout: float) -> tuple[Path, Path]:
    _require_linux()
    if not app_root.is_absolute() or app_root == Path("/"):
        parser.error("--app-root must be a non-root absolute path")
    if timeout <= 0:
        parser.error("--timeout must be positive")
    incoming = app_root / "incoming"
    activation = app_root / "activation"
    if not incoming.is_dir() or not activation.is_dir():
        parser.error("rootless deployment is not bootstrapped; run sudo ./deploy.sh bootstrap-rootless once")
    return incoming, activation


def _submit_local_request(parser: argparse.ArgumentParser, activation: Path, request_id: str, payload: dict) -> None:
    """Publish one request atomically; the activator's path unit fires when it appears."""
    request = activation / "request.json"
    temporary = activation / f".request-{request_id}.json"
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(payload, output, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o640)
    try:
        os.link(temporary, request)
    except FileExistsError:
        parser.error("another local activation request is already pending")
    finally:
        temporary.unlink(missing_ok=True)


def _await_local_result(activation: Path, request_id: str, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    result_path = activation / "result.json"
    while time.monotonic() < deadline:
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, UnicodeError, json.JSONDecodeError):
            result = None
        if isinstance(result, dict) and result.get("release_id") == request_id:
            return result
        time.sleep(0.25)
    print(f"Request timed out; inspect journalctl -u {ACTIVATION_UNIT}", file=sys.stderr)
    return None


def deploy_local(argv: list[str]) -> int:
    """Stage one immutable wheel for the root-owned local release activator."""
    parser = argparse.ArgumentParser(
        prog="bad-decisions deploy local",
        description="Deploy this installed version through a previously bootstrapped, rootless local activator.",
    )
    parser.add_argument("--wheel", type=Path, help="server wheel (default: ./dist's matching wheel, else this version's wheel from PyPI)")
    parser.add_argument("--requirements", type=Path, help="pinned dependency lock (default: the lock shipped with this version)")
    parser.add_argument("--app-root", type=Path, default=Path("/opt/bad-decisions"))
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    incoming, activation = _local_inbox(parser, args.app_root, args.timeout)
    expected = f"bad_decisions-{__version__}-py3-none-any.whl"
    if args.wheel is not None:
        if not args.wheel.is_file():
            parser.error(f"wheel not found: {args.wheel}")
        if args.wheel.name != expected:
            parser.error(f"wheel must be the running command's {__version__} release ({expected})")
    lock = args.requirements or _release_lock()
    if lock is None or not lock.is_file():
        parser.error(f"requirements lock not found: {lock or 'not shipped with this install'}; pass --requirements")

    with tempfile.TemporaryDirectory(prefix="bad-decisions-wheel-") as downloads:
        wheel = args.wheel or Path.cwd() / "dist" / expected
        if args.wheel is None and not wheel.is_file():
            print(f"Downloading {expected} from PyPI", file=sys.stderr)
            try:
                wheel = _download_release_wheel(expected, Path(downloads))
            except RuntimeError as exc:
                print(f"bad-decisions deploy local: {exc}", file=sys.stderr)
                return 1
        release_id = _local_request_id()
        stage = incoming / release_id
        try:
            stage.mkdir(mode=0o750)
            digests = {}
            for source_path, name in ((wheel, wheel.name), (lock, "requirements.lock")):
                staged = stage / name
                with source_path.open("rb") as source, staged.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                staged.chmod(0o640)
                digests[name] = hashlib.sha256(staged.read_bytes()).hexdigest()
            _submit_local_request(parser, activation, release_id, {
                "release_id": release_id,
                "wheel": wheel.name,
                "sha256": digests[wheel.name],
                "requirements_sha256": digests["requirements.lock"],
                "version": __version__,
            })
        except PermissionError:
            shutil.rmtree(stage, ignore_errors=True)
            parser.error("cannot stage a release; log out and back in after bootstrap so the deployment group applies")

    result = _await_local_result(activation, release_id, args.timeout)
    if result is None:
        return 1
    if result.get("status") == "ok":
        print(f"Deployment complete: bad-decisions {result.get('version', __version__)} ({release_id})")
        return 0
    print(f"Deployment failed: {result.get('message', 'activation failed')}", file=sys.stderr)
    return 1


def rollback_local(argv: list[str]) -> int:
    """Ask the root-owned local activator to switch back to a known-good release."""
    parser = argparse.ArgumentParser(
        prog="bad-decisions rollback local",
        description="Roll back a rootless local deployment, with the same rules as sudo ./deploy.sh rollback.",
    )
    parser.add_argument("release_id", nargs="?", help="release under APP_ROOT/releases (default: the newest known-good release that is not current)")
    parser.add_argument("--app-root", type=Path, default=Path("/opt/bad-decisions"))
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    _incoming, activation = _local_inbox(parser, args.app_root, args.timeout)
    request_id = _local_request_id()
    try:
        _submit_local_request(parser, activation, request_id, {"action": "rollback", "request_id": request_id, "target": args.release_id})
    except PermissionError:
        parser.error("cannot submit a rollback; log out and back in after bootstrap so the deployment group applies")
    result = _await_local_result(activation, request_id, args.timeout)
    if result is None:
        return 1
    if result.get("status") == "ok":
        print(f"Rollback complete: serving {result.get('target')} (was {result.get('previous')})")
        return 0
    print(f"Rollback failed: {result.get('message', 'activation failed')}", file=sys.stderr)
    return 1


def _read_aws_env() -> dict[str, str]:
    if not AWS_ENV.is_file():
        return {}
    return dict(line.split("=", 1) for line in AWS_ENV.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))


def deploy_aws(argv: list[str], *, confirm=input) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions deploy aws", description="Build and push the release image, show the CDK diff, and deploy it.")
    parser.add_argument("--image", help="deploy an already-pushed image instead of building one")
    parser.add_argument("--source", type=Path, help="source checkout to build instead of the installed PyPI release")
    parser.add_argument("--yes", action="store_true", help="deploy without asking after the diff")
    parser.add_argument("--desired-count", type=int, default=1)
    parser.add_argument("--max-count", type=int, default=2)
    parser.add_argument("--capacity", choices=("spot", "on-demand"), help="Fargate capacity (saved; default spot, about 70%% cheaper but interruptible)")
    _add_domain_arguments(parser)
    args = parser.parse_args(argv)
    if args.desired_count < 1 or args.max_count < args.desired_count:
        parser.error("counts must be positive and --max-count must be at least --desired-count")
    if args.image and args.source:
        parser.error("--image and --source are mutually exclusive")
    if args.source is not None and not (args.source / "Dockerfile").is_file():
        parser.error("--source must contain Dockerfile")
    values = _read_aws_env()
    if not values:
        print(f"Missing {AWS_ENV}; run bad-decisions setup aws first.", file=sys.stderr); return 2
    _merge_domain_arguments(args, values)
    if args.capacity:
        values["BAD_DECISIONS_CAPACITY"] = args.capacity
    if not values.get("BAD_DECISIONS_CERTIFICATE_ARN") and values.get("BAD_DECISIONS_ALLOW_HTTP") != "1":
        parser.error("HTTPS requires --certificate-arn; use --allow-http only for a disposable smoke test")
    if values.get("BAD_DECISIONS_CERTIFICATE_ARN") and not values.get("BAD_DECISIONS_DOMAIN_NAME"):
        parser.error("HTTPS requires --domain-name so the certificate matches the public endpoint")
    if not args.yes and not sys.stdin.isatty():
        parser.error("no terminal to confirm the diff; pass --yes to deploy unattended")
    _require_commands(parser, "aws", "npx", *(() if args.image else ("docker",)))
    aws_env = _aws_env(values["AWS_PROFILE"], values["AWS_DEFAULT_REGION"])
    # Build every deploy so the image always matches the installed package that defines the stack.
    image = args.image or _publish_image(aws_env, values, args.source)
    if not image: return 1
    env = os.environ.copy(); env.update(values); env.update({"BAD_DECISIONS_IMAGE": image, "BAD_DECISIONS_DESIRED_COUNT": str(args.desired_count), "BAD_DECISIONS_MAX_COUNT": str(args.max_count)})
    cdk = ["npx", "--yes", "aws-cdk", "-a", f"{sys.executable} -m bad_decisions.aws_cdk_app"]
    if _run(cdk + ["diff", "BadDecisionsHybrid"], env=env).returncode:
        print("cdk diff failed; nothing was deployed.", file=sys.stderr); return 1
    if not args.yes and confirm("Deploy these changes? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Deploy cancelled; nothing was deployed.", file=sys.stderr); return 1
    with tempfile.NamedTemporaryFile(prefix="bad-decisions-cdk-", suffix=".json") as outputs:
        completed = _run(cdk + ["deploy", "BadDecisionsHybrid", "--require-approval", "never", "--outputs-file", outputs.name], env=env)
        if completed.returncode:
            return completed.returncode
        deployed = json.loads(Path(outputs.name).read_text(encoding="utf-8"))["BadDecisionsHybrid"]
    values.update({
        "BAD_DECISIONS_IMAGE": image,
        "BAD_DECISIONS_AWS_ENDPOINT": deployed["PublicApiUrl"],
        "BAD_DECISIONS_AWS_PACK_BUCKET": deployed["PackBucketName"],
        "BAD_DECISIONS_AWS_PACK_BASE_URL": f"https://{deployed['PackDistributionDomainName']}",
        "BAD_DECISIONS_AWS_PACK_INDEX": f"https://{deployed['PackDistributionDomainName']}/packs/index",
        "BAD_DECISIONS_AWS_CLUSTER": deployed["ClusterName"],
        "BAD_DECISIONS_AWS_SERVICE": deployed["ServiceName"],
        "BAD_DECISIONS_AWS_CONSEQUENCES_TABLE": deployed["ConsequencesTableName"],
    })
    if deployed.get("ApiDomainTarget"):
        values["BAD_DECISIONS_AWS_DOMAIN_TARGET"] = deployed["ApiDomainTarget"]
    _write_aws_env(values)
    _seed_bundled_aws(values)
    print(f"AWS service ready: {values['BAD_DECISIONS_AWS_ENDPOINT']}")
    print(f"AWS pack index: {values['BAD_DECISIONS_AWS_PACK_INDEX']}")
    return 0


def _aws_coordinates() -> tuple[dict[str, str], str, str]:
    values = _read_aws_env()
    bucket = values.get("BAD_DECISIONS_AWS_PACK_BUCKET")
    base = values.get("BAD_DECISIONS_AWS_PACK_BASE_URL")
    if not bucket or not base:
        raise RuntimeError(f"Missing AWS pack storage in {AWS_ENV}; deploy AWS first.")
    return values, bucket, base


def publish_aws_pack(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions pack publish-aws", description="Publish a validated CardDeck to the AWS runtime and public catalog.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--no-reload", action="store_true", help="do not roll ECS tasks after publishing")
    args = parser.parse_args(argv)
    values, bucket, base = _aws_coordinates()
    from .aws_archive import force_runtime_reload, publish_archive
    metadata = publish_archive(args.archive, bucket=bucket, public_base_url=base, profile=values.get("AWS_PROFILE"), region=values.get("AWS_DEFAULT_REGION"))
    if not args.no_reload:
        cluster, service = values.get("BAD_DECISIONS_AWS_CLUSTER"), values.get("BAD_DECISIONS_AWS_SERVICE")
        if not cluster or not service:
            raise RuntimeError("AWS cluster/service outputs are missing; deploy AWS again.")
        force_runtime_reload(cluster=cluster, service=service, profile=values.get("AWS_PROFILE"), region=values.get("AWS_DEFAULT_REGION"))
    print(f"published {metadata['archive']['pack_id']} to {metadata['url']}")
    return 0


def seed_aws_packs(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions pack seed-aws", description="Seed missing bundled packs into the configured AWS deployment.")
    parser.parse_args(argv)
    values, _bucket, _base = _aws_coordinates()
    count = _seed_bundled_aws(values)
    if not count:
        print("AWS bundled packs are already present.")
    return 0


def list_aws_packs(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions pack list-aws", description="Read the AWS CardDeck catalog.")
    parser.parse_args(argv)
    values, bucket, _base = _aws_coordinates()
    from .aws_archive import read_catalog
    print(json.dumps(read_catalog(bucket=bucket, profile=values.get("AWS_PROFILE"), region=values.get("AWS_DEFAULT_REGION")), sort_keys=True))
    return 0


def consequences_report_aws(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions consequences report aws", description="Read the private AWS Consequences report.")
    parser.parse_args(argv)
    values = _read_aws_env()
    table = values.get("BAD_DECISIONS_AWS_CONSEQUENCES_TABLE")
    if not table:
        print(f"Missing AWS Consequences table in {AWS_ENV}; deploy AWS first.", file=sys.stderr)
        return 2
    old_profile, old_region = os.environ.get("AWS_PROFILE"), os.environ.get("AWS_DEFAULT_REGION")
    try:
        if values.get("AWS_PROFILE"): os.environ["AWS_PROFILE"] = values["AWS_PROFILE"]
        if values.get("AWS_DEFAULT_REGION"): os.environ["AWS_DEFAULT_REGION"] = values["AWS_DEFAULT_REGION"]
        from .aws_consequences import DynamoConsequencesStore
        from .aws_credentials import local_session
        client = local_session(values.get("AWS_PROFILE"), values.get("AWS_DEFAULT_REGION")).client("dynamodb")
        print(json.dumps(DynamoConsequencesStore(table, client=client).report(), sort_keys=True))
    finally:
        if old_profile is None: os.environ.pop("AWS_PROFILE", None)
        else: os.environ["AWS_PROFILE"] = old_profile
        if old_region is None: os.environ.pop("AWS_DEFAULT_REGION", None)
        else: os.environ["AWS_DEFAULT_REGION"] = old_region
    return 0


def _seed_bundled_aws(values: dict[str, str]) -> int:
    from .archive import export_pack
    from .aws_archive import archive_exists, force_runtime_reload, publish_archive
    bucket, base = values["BAD_DECISIONS_AWS_PACK_BUCKET"], values["BAD_DECISIONS_AWS_PACK_BASE_URL"]
    profile, region = values.get("AWS_PROFILE"), values.get("AWS_DEFAULT_REGION")
    missing = []
    registry = load_registry()
    with tempfile.TemporaryDirectory(prefix="bad-decisions-seed-") as directory:
        for pack_id, pack in registry.packs.items():
            if archive_exists(bucket=bucket, pack_id=pack_id, profile=profile, region=region):
                continue
            archive = export_pack(pack, Path(directory) / f"{pack_id}.carddeck")
            publish_archive(archive, bucket=bucket, public_base_url=base, profile=profile, region=region)
            missing.append(pack_id)
    if missing:
        force_runtime_reload(cluster=values["BAD_DECISIONS_AWS_CLUSTER"], service=values["BAD_DECISIONS_AWS_SERVICE"], profile=profile, region=region)
        print(f"Seeded AWS with {len(missing)} bundled pack(s).")
    return len(missing)


def status_aws(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bad-decisions status aws", description="Read authenticated AWS service status.")
    parser.parse_args(argv)
    values = _read_aws_env()
    endpoint = values.get("BAD_DECISIONS_AWS_ENDPOINT")
    token = values.get("BAD_DECISIONS_AWS_MANAGEMENT_TOKEN")
    if not endpoint or not token:
        print(f"Missing AWS endpoint or token in {AWS_ENV}; deploy AWS first.", file=sys.stderr)
        return 2
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/v1/manage/status",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"AWS service returned HTTP {exc.code}.", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"AWS service is unavailable: {exc.reason}", file=sys.stderr)
        return 1
    return 0


def _usage_error(message: str) -> int:
    print(f"bad-decisions: {message}", file=sys.stderr)
    return 2


def run(argv: list[str]) -> int:
    if not argv:
        return 2
    command, rest = argv[0], argv[1:]
    if command == "setup":
        return setup_aws(rest[1:]) if rest and rest[0] == "aws" else setup(rest)
    if command == "serve":
        return serve(rest)
    if command == "deploy":
        if rest and rest[0] == "aws":
            return deploy_aws(rest[1:])
        if rest and rest[0] == "local":
            return deploy_local(rest[1:])
        return deploy(rest)
    if command == "rollback":
        return rollback_local(rest[1:]) if rest and rest[0] == "local" else _usage_error("rollback requires local (privileged installs use sudo ./deploy.sh rollback)")
    if command == "rotate-token":
        return rotate_token_aws(rest[1:]) if rest and rest[0] == "aws" else _usage_error("rotate-token requires aws")
    if command == "status":
        return status_aws(rest[1:]) if rest and rest[0] == "aws" else _service("status")
    if command == "reload":
        return _service("reload", require_root=True)
    if command == "stop":
        return _service("stop", require_root=True)
    raise ValueError(f"unknown operational command: {command}")
