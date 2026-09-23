from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import signal
import sys
import time
from pathlib import Path
from typing import Sequence

from .archive import export_pack, import_pack, initialize_registry, validate_archive
from .consequences import ConsequencesStore
from .engine import generate_from_resolved, render_round
from .errors import BadDecisionsError, PackConfigurationError, UnknownPackError
from .packs import load_registry, resolve_pools
from .remote import import_index, import_url
from . import operations


class CliArgumentError(ValueError):
    pass


def positive_finite(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Deal hands and manage a Bad Decisions service.")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--oneshot", action="store_true", help="print one rendered round and exit")
    mode.add_argument("--rapid", action="store_true", help="continuously print completed rounds")
    result.add_argument("--delay", type=positive_finite, help="positive seconds between rapid rounds (default: 1.0)")
    result.add_argument("--packs", help="comma-separated pack IDs for both colors (default: all packs)")
    result.add_argument("--black-packs", help="override the black-card selector")
    result.add_argument("--white-packs", help="override the white-card selector")
    result.add_argument("--list-packs", action="store_true", help="list available packs and exit")
    return result


def pack_parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="bad-decisions pack", description="Validate, export, or import portable .carddeck archives.")
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate an archive without changing disk")
    validate.add_argument("archive", type=Path)
    export = commands.add_parser("export", help="export one registered pack to an archive")
    export.add_argument("pack_id")
    export.add_argument("archive", type=Path)
    imported = commands.add_parser("import", help="import an archive into an absolute pack registry directory")
    source = imported.add_mutually_exclusive_group()
    source.add_argument("--remote", action="store_true", help="download one HTTPS .carddeck archive")
    source.add_argument("--index", action="store_true", help="import selected packs from a live HTTPS CardDeck index")
    imported.add_argument("archive")
    imported.add_argument("registry_dir", type=Path)
    imported.add_argument("--pack", dest="pack_ids", action="append", help="catalog pack ID; repeat with --index")
    initialized = commands.add_parser("init-registry", help="initialize an empty absolute registry with bundled packs")
    initialized.add_argument("registry_dir", type=Path)
    return result

def consequences_parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="bad-decisions consequences", description="Operate private local Consequences analytics.")
    commands = result.add_subparsers(dest="command", required=True)
    for name, help_text in (("report", "print aggregate report as JSON"), ("rebuild", "rebuild aggregate counters"), ("purge", "purge retained records")):
        command = commands.add_parser(name, help=help_text)
        if name == "report":
            command.add_argument("database", type=Path, nargs="?", help="absolute SQLite database path")
        else:
            command.add_argument("database", type=Path, help="absolute SQLite database path")
        if name == "purge": command.add_argument("--retention-days", type=int, required=True)
    return result


def _write(text: str = "", *, stream=sys.stdout, flush: bool = False) -> None:
    print(text, file=stream, flush=flush)


def _interactive(resolved, registry) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        _write("Interactive mode requires a terminal; use --oneshot or --rapid.", stream=sys.stderr)
        return 2
    while True:
        generated = generate_from_resolved(resolved, registry)
        _write()
        _write("═" * 72)
        _write(generated.black.repr)
        try:
            command = input("\nPress Enter to reveal, or q to quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _write()
            return 0
        if command == "q":
            return 0
        _write()
        for answer in generated.white:
            _write(f"⬜ {answer.text}")
        _write(f"\n→ {generated.result}")
        try:
            command = input("\nPress Enter for another round, or q to quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _write()
            return 0
        if command == "q":
            return 0


def _run_pack(argv: Sequence[str]) -> int:
    args = pack_parser().parse_args(argv)
    if args.command == "validate":
        pack = validate_archive(args.archive)
        _write(f"valid carddeck: {pack.metadata.id}")
        return 0
    if args.command == "export":
        registry = load_registry()
        if args.pack_id not in registry.packs:
            raise UnknownPackError(f"Unknown pack: {args.pack_id}", {"available_packs": list(registry.ids)})
        target = export_pack(registry.packs[args.pack_id], args.archive)
        _write(f"exported {args.pack_id} to {target}")
        return 0
    if args.command == "init-registry":
        copied = initialize_registry(args.registry_dir)
        _write(f"initialized registry with {len(copied)} bundled packs at {args.registry_dir}")
        return 0
    if args.remote:
        if args.pack_ids:
            raise PackConfigurationError("--pack may only be used with --index")
        target = import_url(args.archive, args.registry_dir)
    elif args.index:
        targets = import_index(args.archive, args.registry_dir, pack_ids=args.pack_ids or ())
        for target in targets:
            _write(f"imported {target.stem} to {target}")
        return 0
    elif args.pack_ids:
        raise PackConfigurationError("--pack may only be used with --index")
    else:
        target = import_pack(args.archive, args.registry_dir)
    _write(f"imported {target.stem} to {target}")
    return 0


def _default_consequences_database() -> Path:
    configured = os.getenv("BAD_DECISIONS_CONSEQUENCES_DB")
    if configured:
        database = Path(configured)
        if not database.is_absolute():
            raise PackConfigurationError("BAD_DECISIONS_CONSEQUENCES_DB must be an absolute path")
        return database

    environment = Path(sys.prefix).resolve()
    release = environment.parent if environment.name == ".venv" else Path()
    if release.parent.name == "releases":
        installed = release.parent.parent / "consequences" / "consequences.sqlite3"
        if installed.is_file():
            return installed
    raise PackConfigurationError(
        "consequences report needs a database path; set BAD_DECISIONS_CONSEQUENCES_DB or pass one explicitly"
    )


def _run_consequences(argv: Sequence[str]) -> int:
    args = consequences_parser().parse_args(argv)
    database = args.database if args.database is not None else _default_consequences_database()
    try:
        store = ConsequencesStore(database)
        if args.command == "report": _write(json.dumps(store.report(), sort_keys=True))
        elif args.command == "rebuild": store.rebuild(); _write("rebuilt Consequences aggregates")
        else: _write(f"purged {store.purge(args.retention_days)} retained rounds")
    except (ValueError, sqlite3.Error) as exc:
        raise PackConfigurationError(f"consequences database error: {exc}") from exc
    return 0

def run(argv: Sequence[str] | None = None, *, sleep=time.sleep) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] in {"setup", "serve", "deploy", "status", "reload", "stop"}:
        return operations.run(values)
    if values and values[0] == "pack":
        return _run_pack(values[1:])
    if values and values[0] in {"analytics", "consequences"}:
        return _run_consequences(values[1:])
    args = parser().parse_args(values)
    if args.delay is not None and not args.rapid:
        parser().error("--delay may only be used with --rapid")
    registry = load_registry()
    if args.list_packs:
        for pack_id, pack in registry.packs.items():
            _write(f"{pack_id}\t{pack.metadata.name}\tblack={len(pack.black)}\twhite={len(pack.white)}")
        return 0
    resolved = resolve_pools(registry, packs=args.packs, black_packs=args.black_packs, white_packs=args.white_packs)
    if args.oneshot:
        _write(generate_from_resolved(resolved, registry).result)
        return 0
    if args.rapid:
        delay = args.delay if args.delay is not None else 1.0
        while True:
            _write(generate_from_resolved(resolved, registry).result, flush=True)
            sleep(delay)
    return _interactive(resolved, registry)


def main(argv: Sequence[str] | None = None) -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        return run(argv)
    except KeyboardInterrupt:
        return 0
    except PackConfigurationError as exc:
        _write(f"configuration error: {exc.message}", stream=sys.stderr)
        return 1
    except BadDecisionsError as exc:
        _write(f"{exc.code}: {exc.message}", stream=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
