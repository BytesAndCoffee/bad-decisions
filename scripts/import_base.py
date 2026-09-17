#!/usr/bin/env python3
"""Layout-aware importer for the pinned official 2019 downloadable PDF.

Requires Poppler's pdftotext. It assigns text blocks to the documented 8x5
card grid, excludes rules/terms pages, checks exact source SHA-256, and emits a
review report. It never downloads inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SOURCE_URL = "https://s3.amazonaws.com/cah/CAH_MainGame.pdf"
SOURCE_SHA256 = "92c8a719c6d7f28b89a87ecbfae780e7d4cd7472c97c7cf1ffe3037c2af89f9b"
NS = {"x": "http://www.w3.org/1999/xhtml"}
BLANK_RE = re.compile(r"_{3,}")


def grid_position(x: float, y: float) -> tuple[int, int]:
    """Map text to its physical 143.2pt card cell, including the center gutter."""
    row = min(4, max(0, int(y // 143.2)))
    if x < 612.0:
        col = min(3, max(0, int(x // 143.2)))
    else:
        col = 4 + min(3, max(0, int((x - 612.5) // 143.2)))
    return row, col


def page_cells(page: ET.Element) -> dict[tuple[int, int], list[str]]:
    cells: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
    for line in page.findall(".//x:line", NS):
        words = [word.text or "" for word in line.findall("x:word", NS)]
        if not words:
            continue
        x = float(line.attrib["xMin"])
        y = float(line.attrib["yMin"])
        row, col = grid_position(x, y)
        cells.setdefault((row, col), []).append((y, x, " ".join(words)))
    return {key: [text for _, _, text in sorted(lines)] for key, lines in cells.items()}


def clean_lines(lines: list[str], *, black: bool) -> tuple[str, int | None]:
    slots = None
    kept = []
    for line in lines:
        compact = re.sub(r"\s+", " ", line).strip()
        match = re.fullmatch(r"PICK\s+(\d+)", compact, re.I)
        if match:
            slots = int(match.group(1))
            continue
        if compact in {"CARDS AGAINST HUMANITY", "CARDSAGAINSTHUMANITY.COM"}:
            continue
        kept.append(compact)
    return " ".join(kept).strip(), slots


def extract(pdf: Path) -> tuple[list[dict], list[dict], list[dict]]:
    with tempfile.TemporaryDirectory() as temp:
        bbox = Path(temp) / "source.html"
        subprocess.run(["pdftotext", "-bbox-layout", str(pdf), str(bbox)], check=True)
        pages = ET.parse(bbox).getroot().findall(".//x:page", NS)
    if len(pages) != 17:
        raise ValueError(f"expected 17 PDF pages, found {len(pages)}")
    white, black, review = [], [], []
    # Page 2 has white cards on its right half; pages 3-14 use the full grid.
    for page_number in range(2, 15):
        cells = page_cells(pages[page_number - 1])
        columns = range(4, 8) if page_number == 2 else range(8)
        for row in range(5):
            for col in columns:
                text, _ = clean_lines(cells.get((row, col), []), black=False)
                if text:
                    white.append({"text": text, "source_ref": f"pdf:p{page_number}:r{row+1}:c{col+1}"})
    for page_number in (15, 16):
        cells = page_cells(pages[page_number - 1])
        for row in range(5):
            for col in range(8):
                display, explicit_slots = clean_lines(cells.get((row, col), []), black=True)
                if not display:
                    continue
                blanks = len(BLANK_RE.findall(display))
                slots = explicit_slots or blanks or 1
                template = BLANK_RE.sub("{}", display)
                if blanks == 0:
                    template += "\n" + "\n".join("{}" for _ in range(slots))
                if blanks and blanks != slots:
                    review.append({"source_ref": f"pdf:p{page_number}:r{row+1}:c{col+1}", "issue": "blank/slot mismatch", "blanks": blanks, "slots": slots, "repr": display})
                black.append({"repr": display, "template": template, "slots": slots, "source_ref": f"pdf:p{page_number}:r{row+1}:c{col+1}"})
    return black, white, review


def build_pack(pdf: Path) -> tuple[dict, dict]:
    actual = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if actual != SOURCE_SHA256:
        raise ValueError(f"source checksum mismatch: expected {SOURCE_SHA256}, got {actual}")
    black, white, review = extract(pdf)
    pack = {
        "schema_version": 1,
        "metadata": {
            "id": "base", "name": "Cards Against Humanity downloadable main game", "description": "Cards from the verified official downloadable main-game PDF.",
            "version": "2019-12-02", "language": "en-US", "custom": False,
            "authors": ["Cards Against Humanity LLC"],
            "attribution": "Cards Against Humanity by Cards Against Humanity LLC, used under CC BY-NC-SA 2.0.",
            "license_id": "CC-BY-NC-SA-2.0", "license_url": "https://creativecommons.org/licenses/by-nc-sa/2.0/",
            "license_notice": "Noncommercial use only; attribution and share-alike apply. No endorsement is implied.",
            "sources": [{"origin": SOURCE_URL, "edition": "Official downloadable main game PDF, created 2019-12-02", "sha256": SOURCE_SHA256, "retrieved": "2026-09-16", "license_evidence": "PDF page 17 Terms of Use; LICENSES/CC-BY-NC-SA-2.0-legalcode.html"}],
            "modifications": ["Layout-aware text extraction; line wrapping collapsed to spaces; printed underscore runs normalized to anonymous placeholders; prompts without printed blanks append answers on new lines."],
        },
        "black": [{"id": f"b{i:03d}", **card, "pack": "base"} for i, card in enumerate(black, 1)],
        "white": [{"id": f"w{i:03d}", **card, "pack": "base"} for i, card in enumerate(white, 1)],
    }
    report = {"source_sha256": actual, "pages": 17, "white_pages": "2 (right half), 3-14", "black_pages": "15-16", "excluded_pages": "1 instructions, rules area on page 2, 17 terms", "black_count": len(black), "white_count": len(white), "review_items": review}
    return pack, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    pack, report = build_pack(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(pack['black'])} black and {len(pack['white'])} white cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
