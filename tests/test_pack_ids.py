from __future__ import annotations

import pytest
from pydantic import ValidationError

from bad_decisions.models import BlackCard, WhiteCard
from conftest import make_pack


def test_card_ids_are_unique_across_colors():
    with pytest.raises(ValidationError, match="duplicate card id"):
        make_pack(
            "p",
            black=(BlackCard(id="same", repr="x", template="{}", slots=1, pack="p"),),
            white=(WhiteCard(id="same", text="x", pack="p"),),
        )
