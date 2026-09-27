from __future__ import annotations

import pytest
from pydantic import ValidationError

from bad_decisions.models import Prompt, Answer
from conftest import make_pack


def test_card_ids_are_unique_across_colors():
    with pytest.raises(ValidationError, match="duplicate card id"):
        make_pack(
            "p",
            prompts=(Prompt(id="same", text="x", template="{}", slots=1, pack="p"),),
            answers=(Answer(id="same", text="x", pack="p"),),
        )
