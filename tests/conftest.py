from __future__ import annotations

from types import MappingProxyType

import pytest

from bad_decisions.models import PACK_SCHEMA_VERSION, Answer, Pack, PackMetadata, Prompt, Source
from bad_decisions.packs import Registry


def make_pack(pack_id: str, *, prompts=(), answers=()) -> Pack:
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
    return Pack(schema_version=PACK_SCHEMA_VERSION, metadata=metadata, prompts=tuple(prompts), answers=tuple(answers))


@pytest.fixture
def registry() -> Registry:
    a = make_pack(
        "a",
        prompts=(Prompt(id="b1", text="One _", template="One {}", slots=1, pack="a"),),
        answers=(
            Answer(id="w1", text="α", pack="a"),
            Answer(id="w2", text="same", pack="a"),
        ),
    )
    b = make_pack(
        "b",
        prompts=(Prompt(id="b1", text="Two _ _", template="Two {} {}", slots=2, pack="b"),),
        answers=(
            Answer(id="w1", text="same", pack="b"),
            Answer(id="w2", text="β", pack="b"),
            Answer(id="w3", text="γ", pack="b"),
        ),
    )
    return Registry(MappingProxyType({"a": a, "b": b}))
