from __future__ import annotations

import random
from typing import Protocol, Sequence

from .errors import RenderError
from .models import BlackCard, PackProvenance, Round, WhiteCard
from .packs import Registry, ResolvedPools


class RandomLike(Protocol):
    def choice(self, seq: Sequence): ...
    def sample(self, population: Sequence, k: int): ...


def render_round(black_card: BlackCard, answers: Sequence[str]) -> str:
    if len(answers) != black_card.slots:
        raise RenderError(f"Expected {black_card.slots} answer(s), got {len(answers)}")
    return black_card.template.format(*answers)


def generate_random_round(
    black_pool: Sequence[BlackCard],
    white_pool: Sequence[WhiteCard],
    *,
    registry: Registry,
    selection,
    rng: RandomLike | None = None,
) -> Round:
    chooser = rng or random.SystemRandom()
    black = chooser.choice(black_pool)
    white = tuple(chooser.sample(white_pool, black.slots))
    represented = sorted({black.pack, *(card.pack for card in white)})
    provenance = {
        pack_id: PackProvenance(
            version=registry.packs[pack_id].metadata.version,
            attribution=registry.packs[pack_id].metadata.attribution,
            license_id=registry.packs[pack_id].metadata.license_id,
            license_url=registry.packs[pack_id].metadata.license_url,
            sources=registry.packs[pack_id].metadata.sources,
        )
        for pack_id in represented
    }
    return Round(
        black=black,
        white=white,
        result=render_round(black, [card.text for card in white]),
        selection=selection,
        provenance=provenance,
    )


def generate_from_resolved(resolved: ResolvedPools, registry: Registry, *, rng: RandomLike | None = None) -> Round:
    return generate_random_round(
        resolved.black, resolved.white, registry=registry, selection=resolved.selection, rng=rng
    )
