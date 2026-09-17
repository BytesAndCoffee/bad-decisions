from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import __version__

DEFAULT_API_URL = "https://bytes.coffee/cah"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Generate a Cards Against Coffee round from an API.")
    result.add_argument("--api-url", default=DEFAULT_API_URL, help="API base URL (default: %(default)s)")
    result.add_argument("--packs", help="comma-separated packs for both colors")
    result.add_argument("--black-packs", help="comma-separated black-card packs")
    result.add_argument("--white-packs", help="comma-separated white-card packs")
    result.add_argument("--list-packs", action="store_true", help="list API pack metadata")
    result.add_argument("--health", action="store_true", help="check API health")
    result.add_argument("--json", action="store_true", help="print the complete JSON response")
    result.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds (default: %(default)s)")
    return result


def _base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("--api-url must start with http:// or https://")
    return url


def _get_json(url: str, *, timeout: float) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": f"cards-against-coffee/{__version__}"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            error = payload.get("error", {})
            message = error.get("message", f"HTTP {exc.code}")
            code = error.get("code")
            raise RuntimeError(f"{code}: {message}" if code else message) from exc
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Cannot reach API: {exc.reason if isinstance(exc, URLError) else exc}") from exc


def _print_packs(packs: list[dict[str, Any]]) -> None:
    for pack in packs:
        counts = pack.get("counts", {})
        print(f"{pack['id']}\t{pack['name']}\tblack={counts.get('black', 0)}\twhite={counts.get('white', 0)}")


def run(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.timeout <= 0:
        parser().error("--timeout must be greater than zero")
    if args.list_packs and args.health:
        parser().error("--list-packs and --health cannot be combined")
    try:
        base = _base_url(args.api_url)
        if args.health:
            payload = _get_json(f"{base}/healthz", timeout=args.timeout)
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload["status"])
            return 0
        if args.list_packs:
            payload = _get_json(f"{base}/v1/packs", timeout=args.timeout)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                _print_packs(payload)
            return 0
        params = {key: value for key, value in {"packs": args.packs, "black_packs": args.black_packs, "white_packs": args.white_packs}.items() if value is not None}
        query = f"?{urlencode(params)}" if params else ""
        payload = _get_json(f"{base}/v1/round{query}", timeout=args.timeout)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload["result"])
        return 0
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"coffee-cards: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
