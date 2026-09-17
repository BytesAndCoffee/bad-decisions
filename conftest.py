from __future__ import annotations

import pytest

from bad_decisions.packs import load_registry


@pytest.fixture
def sample_pack():
    return load_registry().packs["maha"]
