"""Cleaning PYX HTML out of existing archives changes only card text, transactionally."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from bad_decisions.archive import export_pack, validate_archive
from bad_decisions.errors import PackConfigurationError
from bad_decisions.models import Pack
from bad_decisions.pyx_import import PYX_MARKUP_NOTE

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clean_pyx_markup", ROOT / "scripts" / "clean_pyx_markup.py")
clean_pyx_markup = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(clean_pyx_markup)

PYX_ORIGIN = "https://raw.githubusercontent.com/ajanata/PretendYoureXyzzy/ed08e37/cah_cards.sql"


def pack(pack_id: str, *, prompt: str, template: str, answer: str, origin: str = PYX_ORIGIN) -> Pack:
    return Pack.model_validate(
        {
            "schema_version": 2,
            "metadata": {
                "id": pack_id, "name": pack_id, "description": "Test pack", "version": f"card-set-{pack_id}",
                "language": "en", "custom": False, "authors": ["Test Author"], "attribution": "Test attribution",
                "license_id": "CC-BY-NC-SA-3.0", "license_url": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
                "license_notice": "Test notice", "sources": [{"origin": origin}],
                "modifications": ["Earlier conversion."],
            },
            "prompts": [
                {"id": f"{pack_id}-p1", "text": prompt, "template": template, "slots": 1, "pack": pack_id, "source_ref": "pyx-card:1"},
                {"id": f"{pack_id}-p2", "text": "Plain ____.", "template": "Plain {}.", "slots": 1, "pack": pack_id},
            ],
            "answers": [
                {"id": f"{pack_id}-a1", "text": answer, "pack": pack_id, "source_ref": "pyx-card:2"},
                {"id": f"{pack_id}-a2", "text": "Plain answer.", "pack": pack_id},
            ],
        }
    )


def write(directory: Path, value: Pack) -> Path:
    directory.mkdir(exist_ok=True)
    return export_pack(value, directory / f"{value.metadata.id}.carddeck")


def test_only_changed_pyx_archives_are_rewritten_with_text_only_changes(tmp_path):
    source = tmp_path / "source"
    dirty = pack("dirty", prompt="Today on <i>Maury</i>:<br>____&reg;", template="Today on <i>Maury</i>:<br>{}&reg;", answer="Pok&eacute;mon &amp; M&amp;Ms")
    write(source, dirty)
    write(source, pack("clean", prompt="Nothing ____.", template="Nothing {}.", answer="Fine."))
    # Not from PYX: its literal entity text is left alone.
    write(source, pack("other", prompt="Say &reg; ____.", template="Say &reg; {}.", answer="&amp;", origin="https://example.invalid/other"))

    manifest = clean_pyx_markup.clean_directory(source, tmp_path / "out")

    assert [entry["archive"] for entry in manifest["archives"]] == ["dirty.carddeck"]
    assert manifest["archives"][0]["changed_cards"] == 2
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == ["clean-manifest.json", "dirty.carddeck"]
    cleaned = validate_archive(tmp_path / "out" / "dirty.carddeck")
    assert cleaned.prompts[0].text == "Today on Maury:\n____®"
    assert cleaned.prompts[0].template == "Today on Maury:\n{}®"
    assert cleaned.answers[0].text == "Pokémon & M&Ms"
    assert cleaned.metadata.version == "card-set-dirty+plaintext"
    assert cleaned.metadata.modifications == ("Earlier conversion.", PYX_MARKUP_NOTE)
    # Everything except the cleaned text and the recorded change is untouched.
    before = dirty.model_dump(mode="json")
    after = cleaned.model_dump(mode="json")
    for document in (before, after):
        document["metadata"].pop("version"); document["metadata"].pop("modifications")
        document["prompts"][0].pop("text"); document["prompts"][0].pop("template"); document["answers"][0].pop("text")
    assert after == before
    written = json.loads((tmp_path / "out" / "clean-manifest.json").read_text())
    assert written == manifest


def test_unsupported_markup_fails_the_whole_run_without_output(tmp_path):
    source = tmp_path / "source"
    write(source, pack("a-good", prompt="____&reg;", template="{}&reg;", answer="Fine."))
    write(source, pack("b-bad", prompt="<span>____</span>", template="<span>{}</span>", answer="Fine."))
    with pytest.raises(ValueError, match=r"b-bad\.carddeck: .*unsupported HTML markup"):
        clean_pyx_markup.clean_directory(source, tmp_path / "out")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["source"]


def test_refuses_an_existing_destination_and_a_missing_source(tmp_path):
    source = tmp_path / "source"
    write(source, pack("dirty", prompt="____&reg;", template="{}&reg;", answer="Fine."))
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError, match="destination already exists"):
        clean_pyx_markup.clean_directory(source, tmp_path / "out")
    with pytest.raises(ValueError, match="source is not a directory"):
        clean_pyx_markup.clean_directory(tmp_path / "missing", tmp_path / "out2")


def test_hostile_archives_are_rejected_before_anything_is_written(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "evil.carddeck").write_bytes(b"not a zip archive")
    with pytest.raises(PackConfigurationError):
        clean_pyx_markup.clean_directory(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert [path.name for path in tmp_path.iterdir()] == ["source"]


def test_a_cleaned_pack_that_still_has_markup_is_an_error():
    already = pack("again", prompt="____&reg;", template="{}&reg;", answer="Fine.").model_dump(mode="json")
    already["metadata"]["version"] += clean_pyx_markup.VERSION_SUFFIX
    with pytest.raises(ValueError, match="already cleaned"):
        clean_pyx_markup.clean_pack(Pack.model_validate(already))
