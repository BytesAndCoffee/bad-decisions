from __future__ import annotations

from types import MappingProxyType

import pytest

from cah_engine.models import BlackCard, Pack, PackMetadata, Source, WhiteCard
from cah_engine.packs import Registry


def make_pack(pack_id: str, *, black=(), white=()) -> Pack:
    metadata = PackMetadata(
        id=pack_id,
        name=f"{pack_id} pack",
        description="Synthetic test fixture",
        version="1.0.0",
        language="en",
        custom=True,
        authors=("Tests",),
        attribution="Original synthetic fixture",
        license_id="CC0-1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        license_notice="Fixture only",
        sources=(Source(origin="tests"),),
        modifications=(),
    )
    return Pack(schema_version=1, metadata=metadata, black=tuple(black), white=tuple(white))


@pytest.fixture
def registry() -> Registry:
    a = make_pack(
        "a",
        black=(BlackCard(id="b1", repr="One _", template="One {}", slots=1, pack="a"),),
        white=(
            WhiteCard(id="w1", text="α", pack="a"),
            WhiteCard(id="w2", text="same", pack="a"),
        ),
    )
    b = make_pack(
        "b",
        black=(BlackCard(id="b1", repr="Two _ _", template="Two {} {}", slots=2, pack="b"),),
        white=(
            WhiteCard(id="w1", text="same", pack="b"),
            WhiteCard(id="w2", text="β", pack="b"),
            WhiteCard(id="w3", text="γ", pack="b"),
        ),
    )
    return Registry(MappingProxyType({"a": a, "b": b}))
