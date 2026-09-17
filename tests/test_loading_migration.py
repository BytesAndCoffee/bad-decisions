from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from cah_engine.errors import PackConfigurationError
from cah_engine.packs import load_registry

ROOT = Path(__file__).parents[1]


def test_bundled_registry_counts_and_provenance():
    registry = load_registry()
    assert registry.ids == ("base", "maha")
    assert (len(registry.packs["base"].black), len(registry.packs["base"].white)) == (80, 500)
    assert (len(registry.packs["maha"].black), len(registry.packs["maha"].white)) == (27, 52)
    manifest = json.loads((ROOT / "imports/manifest.json").read_text())
    assert manifest["packs"]["base"]["sha256"] == "92c8a719c6d7f28b89a87ecbfae780e7d4cd7472c97c7cf1ffe3037c2af89f9b"
    assert all(card.source_ref for pack in registry.packs.values() for card in (*pack.black, *pack.white))


def test_maha_exact_migration_equivalence():
    source = (ROOT / "imports/original_cah_maha.py").read_text()
    tree = ast.parse(source)
    original_black, original_white = None, None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id == "BLACK_CARDS":
            original_black = [{kw.arg: ast.literal_eval(kw.value) for kw in item.keywords} for item in node.value.elts]
        elif node.targets[0].id == "WHITE_CARDS":
            original_white = ast.literal_eval(node.value)
    pack = json.loads((ROOT / "src/bad_decisions/data/packs/maha.json").read_text())
    migrated_black = [{key: card[key] for key in ("repr", "template", "slots")} for card in pack["black"]]
    migrated_white = [card["text"] for card in pack["white"]]
    assert (migrated_black, migrated_white) == (original_black, original_white)
    canonical = json.dumps({"black": original_black, "white": original_white}, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == "2d13f1b5f5f4e51f5ec57186d34e5ab9cea7d703cda7766ea1ad4941a1b8c3fd"


def test_bad_json_fails_with_filename(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{")
    with pytest.raises(PackConfigurationError, match="broken.json"):
        load_registry(tmp_path.resolve())


def test_relative_external_registry_is_rejected():
    with pytest.raises(PackConfigurationError, match="absolute"):
        load_registry("relative")
