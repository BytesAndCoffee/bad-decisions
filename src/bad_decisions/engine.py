from __future__ import annotations

import random
from typing import Protocol, Sequence

from .errors import RenderError
from .models import Answer, PackProvenance, Prompt, Round
from .packs import Registry, ResolvedPools


class RandomLike(Protocol):
    def choice(self, seq: Sequence): ...
    def sample(self, population: Sequence, k: int): ...


def render_round(prompt: Prompt, answers: Sequence[str]) -> str:
    if len(answers) != prompt.slots:
        raise RenderError(f"Expected {prompt.slots} answer(s), got {len(answers)}")
    return prompt.template.format(*answers)


def generate_random_round(
    prompt_pool: Sequence[Prompt],
    answer_pool: Sequence[Answer],
    *,
    registry: Registry,
    selection,
    rng: RandomLike | None = None,
) -> Round:
    chooser = rng or random.SystemRandom()
    prompt = chooser.choice(prompt_pool)
    answers = tuple(chooser.sample(answer_pool, prompt.slots))
    represented = sorted({prompt.pack, *(card.pack for card in answers)})
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
        prompt=prompt,
        answers=answers,
        result=render_round(prompt, [card.text for card in answers]),
        selection=selection,
        provenance=provenance,
    )


def generate_from_resolved(resolved: ResolvedPools, registry: Registry, *, rng: RandomLike | None = None) -> Round:
    return generate_random_round(
        resolved.prompts, resolved.answers, registry=registry, selection=resolved.selection, rng=rng
    )
