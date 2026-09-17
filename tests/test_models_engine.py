from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from bad_decisions.engine import generate_from_resolved, render_round
from bad_decisions.errors import RenderError
from bad_decisions.models import BlackCard
from bad_decisions.packs import resolve_pools


@pytest.mark.parametrize("slots", [True, 0, -1, 1.0, "1"])
def test_slots_are_strict_positive_integers(slots):
    with pytest.raises(ValidationError):
        BlackCard(id="b", repr="x", template="{}", slots=slots, pack="p")


@pytest.mark.parametrize("template", ["{0}", "{name}", "{.__class__}", "{!r}", "{:>2}", "{"])
def test_forbidden_templates(template):
    with pytest.raises(ValidationError):
        BlackCard(id="b", repr="x", template=template, slots=1, pack="p")


def test_literal_braces_unicode_and_multiline_rendering():
    card = BlackCard(id="b", repr="π", template="literal {{x}}\n{} + {}", slots=2, pack="p")
    assert render_round(card, ["{raw}", "ß"]) == "literal {x}\n{raw} + ß"
    with pytest.raises(RenderError):
        render_round(card, ["one"])


def test_generation_is_deterministic_unique_and_does_not_mutate(registry):
    resolved = resolve_pools(registry, packs="all")
    original_black, original_white = resolved.black, resolved.white
    result = generate_from_resolved(resolved, registry, rng=random.Random(7))
    assert len(result.white) == result.black.slots
    assert len({(x.pack, x.id) for x in result.white}) == len(result.white)
    assert resolved.black == original_black and resolved.white == original_white
    assert tuple(result.selection.black_packs) == ("a", "b")
    assert set(result.provenance) == {result.black.pack, *(x.pack for x in result.white)}


def test_same_text_distinct_identities_can_both_be_drawn(registry):
    resolved = resolve_pools(registry, packs="all", black_packs="b")
    seen = False
    for seed in range(100):
        result = generate_from_resolved(resolved, registry, rng=random.Random(seed))
        if [x.text for x in result.white] == ["same", "same"]:
            seen = True
            assert result.white[0].pack != result.white[1].pack
            break
    assert seen
