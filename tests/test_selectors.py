from __future__ import annotations

import pytest

from bad_decisions.errors import SelectorError, UnknownPackError
from bad_decisions.packs import resolve_pools


def identities(cards):
    return [(x.pack, x.id) for x in cards]


def test_default_and_independent_overrides(registry):
    default = resolve_pools(registry)
    assert default.selection.prompt_packs == ("a", "b")
    assert default.selection.answer_packs == ("a", "b")
    with pytest.raises(UnknownPackError):
        resolve_pools(registry, packs="base")
    resolved = resolve_pools(registry, packs="a", prompt_packs="b", answer_packs=" a, b,a ")
    assert identities(resolved.prompts) == [("b", "b1")]
    assert identities(resolved.answers) == [("a", "w1"), ("a", "w2"), ("b", "w1"), ("b", "w2"), ("b", "w3")]


def test_all_sorted_and_duplicates_removed(registry):
    assert resolve_pools(registry, packs="all").selection.prompt_packs == ("a", "b")
    assert resolve_pools(registry, packs="b,b,a").selection.prompt_packs == ("b", "a")


@pytest.mark.parametrize("value", ["", "a,", ",a", "a,,b", "all,a", "A", "typo"])
def test_bad_selectors(registry, value):
    with pytest.raises(SelectorError):
        resolve_pools(registry, packs=value)


def test_overridden_base_selector_is_still_validated(registry):
    with pytest.raises(UnknownPackError):
        resolve_pools(registry, packs="typo", prompt_packs="a", answer_packs="b")
