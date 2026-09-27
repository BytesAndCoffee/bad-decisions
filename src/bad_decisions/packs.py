from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from pydantic import ValidationError

from .errors import EmptyPoolError, InsufficientCapacityError, PackConfigurationError, SelectorError, UnknownPackError
from .models import Answer, Pack, Prompt, Selection


@dataclass(frozen=True)
class Registry:
    packs: Mapping[str, Pack]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self.packs)


@dataclass(frozen=True)
class ResolvedPools:
    prompts: tuple[Prompt, ...]
    answers: tuple[Answer, ...]
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
    configured = pack_dir if pack_dir is not None else os.getenv("BAD_DECISIONS_PACK_DIR")
    bucket = os.getenv("BAD_DECISIONS_PACK_BUCKET") if configured is None else None
    if bucket:
        from .aws_packs import load_s3_packs
        prefix = os.getenv("BAD_DECISIONS_PACK_PREFIX", "packs/")
        remote = load_s3_packs(bucket, prefix)
        if remote:
            return Registry(remote)
        # A new AWS deployment starts before `pack seed-aws` fills the bucket, so
        # this falls back to the bundled packs, but never silently.
        logging.getLogger("bad_decisions.packs").warning(
            "no packs found in s3://%s/%s; serving the bundled packs until the bucket is seeded (bad-decisions pack seed-aws)", bucket, prefix
        )
    if configured is not None:
        root = Path(configured)
        if not root.is_absolute():
            raise PackConfigurationError("BAD_DECISIONS_PACK_DIR must be an absolute path")
        if not root.is_dir():
            raise PackConfigurationError(f"pack directory does not exist: {root}")
        paths = sorted(root.glob("*.json"))
    else:
        resource = files("bad_decisions").joinpath("data/packs")
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
    registry: Registry, *, packs: str | None = None, prompt_packs: str | None = None, answer_packs: str | None = None
) -> ResolvedPools:
    base_ids = registry.ids if packs is None else _parse_selector(packs, registry, "packs")
    prompt_ids = _parse_selector(prompt_packs, registry, "prompt") if prompt_packs is not None else base_ids
    answer_ids = _parse_selector(answer_packs, registry, "answer") if answer_packs is not None else base_ids
    prompts = tuple(card for pack_id in prompt_ids for card in registry.packs[pack_id].prompts)
    answers = tuple(card for pack_id in answer_ids for card in registry.packs[pack_id].answers)
    if not prompts:
        raise EmptyPoolError("The selected packs have no prompts", {"side": "prompt", "available_packs": list(registry.ids)})
    if not answers:
        raise EmptyPoolError("The selected packs have no answers", {"side": "answer", "available_packs": list(registry.ids)})
    required = max(card.slots for card in prompts)
    if len(answers) < required:
        raise InsufficientCapacityError(
            f"The selected packs have {len(answers)} answers but a selected prompt needs {required}",
            {"available": len(answers), "required": required},
        )
    return ResolvedPools(prompts, answers, Selection(prompt_packs=prompt_ids, answer_packs=answer_ids))
