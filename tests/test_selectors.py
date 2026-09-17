from __future__ import annotations

import pytest

from cah_engine.errors import SelectorError, UnknownPackError
from cah_engine.packs import resolve_pools


def identities(cards):
    return [(x.pack, x.id) for x in cards]


def test_default_and_independent_overrides(registry):
    with pytest.raises(UnknownPackError):
        resolve_pools(registry)
    resolved = resolve_pools(registry, packs="a", black_packs="b", white_packs=" a, b,a ")
    assert identities(resolved.black) == [("b", "b1")]
    assert identities(resolved.white) == [("a", "w1"), ("a", "w2"), ("b", "w1"), ("b", "w2"), ("b", "w3")]


def test_all_sorted_and_duplicates_removed(registry):
    assert resolve_pools(registry, packs="all").selection.black_packs == ("a", "b")
    assert resolve_pools(registry, packs="b,b,a").selection.black_packs == ("b", "a")


@pytest.mark.parametrize("value", ["", "a,", ",a", "a,,b", "all,a", "A", "typo"])
def test_bad_selectors(registry, value):
    with pytest.raises(SelectorError):
        resolve_pools(registry, packs=value)


def test_overridden_base_selector_is_still_validated(registry):
    with pytest.raises(UnknownPackError):
        resolve_pools(registry, packs="typo", black_packs="a", white_packs="b")
