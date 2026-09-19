"""Convert a locally supplied Pretend You're Xyzzy PostgreSQL card dump.

The SQL dump remains an external source.  This module never downloads it and
does not make its card data part of the Bad Decisions distribution.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .archive import export_pack
from .errors import PackConfigurationError
from .models import Pack

LICENSE_ID = "CC-BY-NC-SA-3.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/3.0/"
LICENSE_NOTICE = (
    "CC-BY-NC-SA-3.0 — Creative Commons Attribution-NonCommercial-ShareAlike 3.0 Unported; "
    "attribution, non-commercial use, and share-alike are required. "
    f"License: {LICENSE_URL}"
)
COPY_START = re.compile(r"^COPY (?P<table>[a-z_]+) \((?P<columns>[a-z_, ]+)\) FROM stdin;$")
OCTAL_ESCAPE = re.compile(r"[0-7]{1,3}", re.ASCII)
HEX_ESCAPE = re.compile(r"x(?P<digits>[0-9A-Fa-f]{1,2})", re.ASCII)
REQUIRED_TABLES = frozenset(
    {"black_cards", "white_cards", "card_set", "card_set_black_card", "card_set_white_card"}
)


@dataclass(frozen=True)
class CardSet:
    id: int
    active: bool
    base_deck: bool
    description: str
    name: str
    weight: int


def _error(message: str) -> PackConfigurationError:
    return PackConfigurationError(f"pyx import: {message}")


def _decode_copy_value(value: str) -> str | None:
    if value == r"\N":
        return None
    if "\\" not in value:
        return value
    output: list[str] = []
    # PostgreSQL COPY writes non-ASCII text as byte escapes (\303\251), so consecutive octal/hex escapes are
    # collected as bytes and decoded together as strict UTF-8 whenever any other output is appended.
    pending = bytearray()

    def flush() -> None:
        if not pending:
            return
        try:
            output.append(pending.decode("utf-8"))
        except UnicodeDecodeError as exc:
            # Raised here rather than propagated: convert() reports UnicodeDecodeError as a bad dump encoding.
            raise _error(f"invalid UTF-8 in COPY byte escapes: {bytes(pending)!r}") from exc
        pending.clear()

    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\":
            flush()
            output.append(char)
            index += 1
            continue
        if index + 1 >= len(value):
            raise _error("truncated PostgreSQL COPY escape")
        escaped = value[index + 1]
        simple = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}
        if escaped in simple:
            flush()
            output.append(simple[escaped])
            index += 2
            continue
        octal = OCTAL_ESCAPE.match(value, index + 1)
        if octal:
            code = int(octal.group(), 8)
            # Deliberately stricter than PostgreSQL, which wraps values above \377 to a single byte: wrapping
            # would silently coerce malformed data, so it is rejected instead.
            if code > 0o377:
                raise _error(f"octal COPY escape out of range: \\{octal.group()}")
            pending.append(code)
            index += 1 + octal.end() - octal.start()
            continue
        hexadecimal = HEX_ESCAPE.match(value, index + 1)
        if hexadecimal:
            pending.append(int(hexadecimal.group("digits"), 16))
            index += 1 + hexadecimal.end() - hexadecimal.start()
            continue
        flush()
        output.append(escaped)
        index += 2
    flush()
    return "".join(output)


def parse_copy_tables(payload: str) -> dict[str, tuple[dict[str, str | None], ...]]:
    """Read the COPY sections needed from a PostgreSQL text dump strictly."""

    tables: dict[str, list[dict[str, str | None]]] = {}
    # Split on "\n" only: str.splitlines() would also break rows on U+2028, U+0085, \x0b, \x0c and
    # \x1c-\x1e, which PostgreSQL COPY leaves raw inside card text. One trailing "\r" (CRLF dump) is dropped.
    lines = iter(line[:-1] if line.endswith("\r") else line for line in payload.split("\n"))
    for line in lines:
        match = COPY_START.fullmatch(line)
        if not match or match.group("table") not in REQUIRED_TABLES:
            continue
        name = match.group("table")
        if name in tables:
            raise _error(f"duplicate COPY section for {name}")
        columns = tuple(column.strip() for column in match.group("columns").split(","))
        rows: list[dict[str, str | None]] = []
        for row in lines:
            if row == r"\.":
                break
            values = row.split("\t")
            if len(values) != len(columns):
                raise _error(f"{name}: expected {len(columns)} columns, found {len(values)}")
            rows.append(dict(zip(columns, (_decode_copy_value(value) for value in values), strict=True)))
        else:
            raise _error(f"{name}: unterminated COPY section")
        tables[name] = rows
    missing = sorted(REQUIRED_TABLES - tables.keys())
    if missing:
        raise _error(f"missing required COPY sections: {', '.join(missing)}")
    return {name: tuple(rows) for name, rows in tables.items()}


def _integer(row: dict[str, str | None], column: str, *, table: str) -> int:
    value = row.get(column)
    if value is None:
        raise _error(f"{table}: missing {column}")
    try:
        return int(value)
    except ValueError as exc:
        raise _error(f"{table}: invalid integer {column}={value!r}") from exc


def _text(row: dict[str, str | None], column: str, *, table: str) -> str:
    value = row.get(column)
    if value is None or not value.strip():
        raise _error(f"{table}: missing {column}")
    return value


def _boolean(row: dict[str, str | None], column: str, *, table: str) -> bool:
    value = row.get(column)
    if value == "t":
        return True
    if value == "f":
        return False
    raise _error(f"{table}: invalid boolean {column}={value!r}")


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result or "unnamed"


def _template(text: str, slots: int) -> str:
    escaped = text.replace("{", "{{").replace("}", "}}")
    if "____" not in text:
        return escaped + "\n" + "\n".join("{}" for _ in range(slots))
    return escaped.replace("____", "{}")


def _source_ref(*, card_id: int, watermark: str | None, draw: int | None = None, pick: int | None = None) -> str:
    values = [f"pyx-card:{card_id}"]
    if watermark:
        values.append(f"watermark:{watermark}")
    if draw is not None:
        values.append(f"draw:{draw}")
    if pick is not None:
        values.append(f"pick:{pick}")
    return ";".join(values)


def convert(payload: bytes, *, source_url: str, retrieved: str | None = None, include_inactive: bool = False) -> tuple[Pack, ...]:
    """Create one validated pack per PYX card set from supplied SQL bytes."""

    if not source_url.strip():
        raise _error("source_url is required")
    try:
        tables = parse_copy_tables(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise _error("SQL dump must be UTF-8") from exc
    black = { _integer(row, "id", table="black_cards"): row for row in tables["black_cards"] }
    white = { _integer(row, "id", table="white_cards"): row for row in tables["white_cards"] }
    card_sets = tuple(
        CardSet(
            id=_integer(row, "id", table="card_set"),
            active=_boolean(row, "active", table="card_set"),
            base_deck=_boolean(row, "base_deck", table="card_set"),
            description=row.get("description") or "",
            name=_text(row, "name", table="card_set"),
            weight=_integer(row, "weight", table="card_set"),
        )
        for row in tables["card_set"]
    )
    black_membership: dict[int, list[int]] = {}
    for row in tables["card_set_black_card"]:
        black_membership.setdefault(_integer(row, "card_set_id", table="card_set_black_card"), []).append(
            _integer(row, "black_card_id", table="card_set_black_card")
        )
    white_membership: dict[int, list[int]] = {}
    for row in tables["card_set_white_card"]:
        white_membership.setdefault(_integer(row, "card_set_id", table="card_set_white_card"), []).append(
            _integer(row, "white_card_id", table="card_set_white_card")
        )
    digest = hashlib.sha256(payload).hexdigest()
    result: list[Pack] = []
    for card_set in card_sets:
        if not include_inactive and not card_set.active:
            continue
        pack_id = f"pyx-{card_set.id}-{_slug(card_set.name)}"
        pack_black: list[dict[str, object]] = []
        for card_id in black_membership.get(card_set.id, []):
            row = black.get(card_id)
            if row is None:
                raise _error(f"card set {card_set.id}: missing black card {card_id}")
            text = _text(row, "text", table="black_cards")
            pick = _integer(row, "pick", table="black_cards")
            if pick < 1 or (text.count("____") and text.count("____") != pick):
                raise _error(f"black card {card_id}: unsupported pick/blank combination")
            pack_black.append(
                {
                    "id": f"pyx-black-{card_id}", "repr": text, "template": _template(text, pick), "slots": pick,
                    "pack": pack_id,
                    "source_ref": _source_ref(card_id=card_id, watermark=row.get("watermark"), draw=_integer(row, "draw", table="black_cards"), pick=pick),
                }
            )
        pack_white: list[dict[str, object]] = []
        for card_id in white_membership.get(card_set.id, []):
            row = white.get(card_id)
            if row is None:
                raise _error(f"card set {card_set.id}: missing white card {card_id}")
            pack_white.append(
                {
                    "id": f"pyx-white-{card_id}", "text": _text(row, "text", table="white_cards"), "pack": pack_id,
                    "source_ref": _source_ref(card_id=card_id, watermark=row.get("watermark")),
                }
            )
        if not pack_black and not pack_white:
            continue
        result.append(Pack.model_validate({
            "schema_version": 1,
            "metadata": {
                "id": pack_id, "name": f"Pretend You're Xyzzy: {card_set.name}",
                "description": card_set.description or f"Card set {card_set.name} imported from a Pretend You're Xyzzy SQL dump.",
                "version": f"card-set-{card_set.id}", "language": "en", "custom": False,
                "authors": ["Andy Janata"],
                "attribution": "Pretend You're Xyzzy cards by Andy Janata, based on Cards Against Humanity materials. Imported by Bad Decisions; no endorsement implied.",
                "license_id": LICENSE_ID, "license_url": LICENSE_URL, "license_notice": LICENSE_NOTICE,
                "sources": [{"origin": source_url, "edition": f"Pretend You're Xyzzy SQL card set {card_set.id}: {card_set.name}", "sha256": digest, "retrieved": retrieved, "license_evidence": "cah_cards.sql header: ‘Pretend You're Xyzzy cards by Andy Janata’ under CC BY-NC-SA 3.0; it states the cards are based on Cards Against Humanity materials."}],
                "modifications": ["Converted PostgreSQL COPY rows to CardDeck fields while preserving card text, order, card-set membership, watermarks, and source identifiers.", "Prompts with no blank receive newline-appended answer placeholders, matching Bad Decisions' existing prompt representation. Prompt draw counts are retained in source_ref; the runtime does not implement draw mechanics."],
            },
            "black": pack_black, "white": pack_white,
        }))
    if not result:
        raise _error("no non-empty card sets selected")
    return tuple(result)


def export_all(packs: Iterable[Pack], destination_dir: str | Path) -> tuple[Path, ...]:
    """Export every converted pack, refusing to overwrite an existing archive."""

    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.is_dir():
        raise _error(f"destination is not a directory: {destination}")
    return tuple(export_pack(pack, destination / f"{pack.metadata.id}.carddeck") for pack in packs)
