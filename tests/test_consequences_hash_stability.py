"""Consequences hashes must survive the 2.0 prompt/answer rename byte for byte.

The golden values were computed by the 1.x code (schema-1 packs, "black"/"white"
names) for fixed rounds of the bundled packs. If these change, every stored
combination splits into a new identity and the analytics history is lost.
"""

from __future__ import annotations

import pytest

from bad_decisions.consequences import round_identity
from bad_decisions.models import Round, Selection
from bad_decisions.packs import load_registry

GOLDEN = [
    ("maha", "b027", ["w001", "w002"],
     "6baa6bd4f713cd7cb4253fac53220ad870f48ad00bca44aec40cbc5d9f55493c",
     ["8c9c58cea2d143ed715bdb0fc1f5776aef50a9ffd2f6daafabf77e025377abc9", "7ca7fc432b7995c15c0937847602fe676b0666032c484bd7b3256fc1c798e8b3"],
     "8d55782f50f518c9f440ccc5d21ee2486cf22f44c90a5a903de77c1c5964df4b"),
    ("base", "b036", ["w001", "w002", "w003"],
     "823cbaae41d3af614482565f958a90621d64b33b742ce37367923d3fa8ee83e2",
     ["4e1a8ed5a7d3a10d3da345549a6e64ff02a2af410cd7da67317cc5fb6f24cddb", "f9a2ff53709b3acd30df1c5876aa017c7a7e03a88c3e9de85878c30c27990b6a",
      "7cbba6e6695a494d0e416f4bd1886b3e0c1daf3bee4f375aca98cc7858a4f17d"],
     "114572ce233801ea128516029cfe6c5532a8391b0b31534da2948cb694e3fae9"),
    ("coffee", "coffee-black-030", ["coffee-white-001", "coffee-white-002"],
     "3dfc6b6576b55240cd76bd5efe30f32b0cdefad609e8c7ac857d568abe2bdb0f",
     ["8f5e0aed111f818c2ff8a0f030bf3163174d1318fb5f13e64faf264ecd18473e", "5e0c8507fab0a845b5be26ef57fe8f0ecc263dcbdce8fa74007410b2ecc1945e"],
     "311e924f51a51c8f9c4e2ffc61577b0e0ba41f5d314ea44b14d64f9303a596d0"),
]


@pytest.mark.parametrize("pack_id,prompt_id,answer_ids,prompt_hash,answer_hashes,combination_hash", GOLDEN, ids=[case[0] for case in GOLDEN])
def test_round_identity_matches_1x(pack_id, prompt_id, answer_ids, prompt_hash, answer_hashes, combination_hash):
    pack = load_registry().packs[pack_id]
    prompt = next(card for card in pack.prompts if card.id == prompt_id)
    answers = tuple(next(card for card in pack.answers if card.id == answer_id) for answer_id in answer_ids)
    round_ = Round(
        prompt=prompt, answers=answers, result="", provenance={},
        selection=Selection(prompt_packs=(pack_id,), answer_packs=(pack_id,)),
    )
    assert round_identity(round_) == (prompt_hash, answer_hashes, combination_hash)
