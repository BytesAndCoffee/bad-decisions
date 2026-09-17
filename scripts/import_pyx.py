#!/usr/bin/env python3
"""Convert a local Pretend You're Xyzzy cah_cards.sql dump to CardDeck archives."""

from __future__ import annotations

import argparse
from pathlib import Path

from bad_decisions.pyx_import import convert, export_all


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sql_dump", type=Path, help="locally acquired cah_cards.sql file")
    parser.add_argument("destination_dir", type=Path, help="directory for generated .carddeck files")
    parser.add_argument("--source-url", required=True, help="immutable URL or commit-pinned source identifier")
    parser.add_argument("--retrieved", help="source retrieval date (YYYY-MM-DD)")
    parser.add_argument("--include-inactive", action="store_true", help="also export inactive PYX card sets")
    args = parser.parse_args()
    payload = args.sql_dump.read_bytes()
    paths = export_all(convert(payload, source_url=args.source_url, retrieved=args.retrieved, include_inactive=args.include_inactive), args.destination_dir)
    print(f"exported {len(paths)} CardDeck archives to {args.destination_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
