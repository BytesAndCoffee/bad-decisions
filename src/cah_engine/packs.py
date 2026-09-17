from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from pydantic import ValidationError

from .errors import EmptyPoolError, InsufficientCapacityError, PackConfigurationError, SelectorError, UnknownPackError
from .models import BlackCard, Pack, Selection, WhiteCard


@dataclass(frozen=True)
class Registry:
    packs: Mapping[str, Pack]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self.packs)


@dataclass(frozen=True)
class ResolvedPools:
    black: tuple[BlackCard, ...]
    white: tuple[WhiteCard, ...]
    selection: Selection


def _load_file(path: Path) -> Pack:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackConfigurationError(f"{path}: cannot read valid UTF-8 JSON: {exc}") from exc
    try:
        return Pack.model_validate(raw)
    except ValidationError as exc:
        raise PackConfigurationError(f"{path}: invalid pack: {exc}") from exc


def load_registry(pack_dir: str | Path | None = None) -> Registry:
    configured = pack_dir if pack_dir is not None else os.getenv("CAH_PACK_DIR")
    if configured is not None:
        root = Path(configured)
        if not root.is_absolute():
            raise PackConfigurationError("CAH_PACK_DIR must be an absolute path")
        if not root.is_dir():
            raise PackConfigurationError(f"pack directory does not exist: {root}")
        paths = sorted(root.glob("*.json"))
    else:
        resource = files("cah_engine").joinpath("data/packs")
        paths = sorted(Path(str(item)) for item in resource.iterdir() if item.name.endswith(".json"))
    if not paths:
        raise PackConfigurationError("no JSON pack files found")
    loaded: dict[str, Pack] = {}
    for path in paths:
        pack = _load_file(path)
        if pack.metadata.id in loaded:
            raise PackConfigurationError(f"duplicate pack id {pack.metadata.id!r}: {path}")
        loaded[pack.metadata.id] = pack
    return Registry(MappingProxyType(dict(sorted(loaded.items()))))


def _parse_selector(value: str, registry: Registry, side: str) -> tuple[str, ...]:
    if not isinstance(value, str) or value == "":
        raise SelectorError(f"{side} selector must not be empty", {"side": side})
    raw = value.split(",")
    if any(not item.strip() for item in raw):
        raise SelectorError(f"{side} selector contains an empty pack ID", {"side": side})
    ids = list(dict.fromkeys(item.strip() for item in raw))
    if "all" in ids:
        if len(ids) != 1:
            raise SelectorError("'all' must be used alone", {"side": side})
        return registry.ids
    unknown = [item for item in ids if item not in registry.packs]
    if unknown:
        name = unknown[0]
        raise UnknownPackError(f"Unknown pack: {name}", {"available_packs": list(registry.ids), "side": side})
    return tuple(ids)


def resolve_pools(
    registry: Registry, *, packs: str = "base", black_packs: str | None = None, white_packs: str | None = None
) -> ResolvedPools:
    base_ids = _parse_selector(packs, registry, "packs")
    black_ids = _parse_selector(black_packs, registry, "black") if black_packs is not None else base_ids
    white_ids = _parse_selector(white_packs, registry, "white") if white_packs is not None else base_ids
    black = tuple(card for pack_id in black_ids for card in registry.packs[pack_id].black)
    white = tuple(card for pack_id in white_ids for card in registry.packs[pack_id].white)
    if not black:
        raise EmptyPoolError("Selected black pool is empty", {"side": "black", "available_packs": list(registry.ids)})
    if not white:
        raise EmptyPoolError("Selected white pool is empty", {"side": "white", "available_packs": list(registry.ids)})
    required = max(card.slots for card in black)
    if len(white) < required:
        raise InsufficientCapacityError(
            f"Selected white pool has {len(white)} cards but black selection requires {required}",
            {"available": len(white), "required": required},
        )
    return ResolvedPools(black, white, Selection(black_packs=black_ids, white_packs=white_ids))
