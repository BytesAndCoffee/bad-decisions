from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from bad_decisions.engine import generate_from_resolved, render_round
from bad_decisions.errors import RenderError
from bad_decisions.models import Prompt
from bad_decisions.packs import resolve_pools


@pytest.mark.parametrize("slots", [True, 0, -1, 1.0, "1"])
def test_slots_are_strict_positive_integers(slots):
    with pytest.raises(ValidationError):
        Prompt(id="b", text="x", template="{}", slots=slots, pack="p")


@pytest.mark.parametrize("template", ["{0}", "{name}", "{.__class__}", "{!r}", "{:>2}", "{"])
def test_forbidden_templates(template):
    with pytest.raises(ValidationError):
        Prompt(id="b", text="x", template=template, slots=1, pack="p")


def test_literal_braces_unicode_and_multiline_rendering():
    card = Prompt(id="b", text="π", template="literal {{x}}\n{} + {}", slots=2, pack="p")
    assert render_round(card, ["{raw}", "ß"]) == "literal {x}\n{raw} + ß"
    with pytest.raises(RenderError):
        render_round(card, ["one"])


def test_generation_is_deterministic_unique_and_does_not_mutate(registry):
    resolved = resolve_pools(registry, packs="all")
    original_prompts, original_answers = resolved.prompts, resolved.answers
    result = generate_from_resolved(resolved, registry, rng=random.Random(7))
    assert len(result.answers) == result.prompt.slots
    assert len({(x.pack, x.id) for x in result.answers}) == len(result.answers)
    assert resolved.prompts == original_prompts and resolved.answers == original_answers
    assert tuple(result.selection.prompt_packs) == ("a", "b")
    assert set(result.provenance) == {result.prompt.pack, *(x.pack for x in result.answers)}


def test_same_text_distinct_identities_can_both_be_drawn(registry):
    resolved = resolve_pools(registry, packs="all", prompt_packs="b")
    seen = False
    for seed in range(100):
        result = generate_from_resolved(resolved, registry, rng=random.Random(seed))
        if [x.text for x in result.answers] == ["same", "same"]:
            seen = True
            assert result.answers[0].pack != result.answers[1].pack
            break
    assert seen
