"""Command-line entry point for explicit remote CardDeck imports."""

from __future__ import annotations

import argparse
from pathlib import Path

from .errors import PackConfigurationError
from .remote import import_index, import_url


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="bad-decisions-remote", description="Safely import public HTTPS CardDeck archives.")
    commands = result.add_subparsers(dest="command", required=True)
    archive = commands.add_parser("archive", help="import one HTTPS .carddeck archive")
    archive.add_argument("url")
    archive.add_argument("registry_dir", type=Path)
    index = commands.add_parser("index", help="import selected packs from a live HTTPS CardDeck index")
    index.add_argument("url")
    index.add_argument("registry_dir", type=Path)
    index.add_argument("--pack", dest="pack_ids", action="append", required=True, help="catalog pack ID; repeat as needed")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "archive":
            target = import_url(args.url, args.registry_dir)
            print(f"imported {target.stem} to {target}")
            return 0
        for target in import_index(args.url, args.registry_dir, pack_ids=args.pack_ids):
            print(f"imported {target.stem} to {target}")
        return 0
    except PackConfigurationError as exc:
        print(f"configuration error: {exc.message}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
