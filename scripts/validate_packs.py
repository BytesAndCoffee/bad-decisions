#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bad_decisions.packs import load_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pack_dir", type=Path, nargs="?")
    args = parser.parse_args()
    registry = load_registry(args.pack_dir.resolve() if args.pack_dir else None)
    print(json.dumps({pack_id: {"black": len(pack.black), "white": len(pack.white)} for pack_id, pack in registry.packs.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
