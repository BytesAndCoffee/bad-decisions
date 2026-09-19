from __future__ import annotations

import zipfile

import pytest

from bad_decisions.errors import PackConfigurationError
from bad_decisions.pyx_import import convert, export_all


SQL = b'''COPY black_cards (id, draw, pick, text, watermark) FROM stdin;
10\t2\t1\tPrompt with ____ and {braces}.\tTST
\\.
COPY white_cards (id, text, watermark) FROM stdin;
20\tA response\\twith a tab.\tTST
\\.
COPY card_set (id, active, base_deck, description, name, weight) FROM stdin;
1\tt\tt\tA provenance test.\tTest Set\t1
2\tf\tf\tAn inactive set.\tInactive\t2
\\.
COPY card_set_black_card (card_set_id, black_card_id) FROM stdin;
1\t10
\\.
COPY card_set_white_card (card_set_id, white_card_id) FROM stdin;
1\t20
\\.
'''


def test_converts_active_card_sets_with_provenance_and_exact_text(tmp_path):
    packs = convert(SQL, source_url="https://example.test/pyx/cah_cards.sql", retrieved="2026-09-17")
    assert len(packs) == 1
    pack = packs[0]
    assert pack.metadata.id == "pyx-1-test-set"
    assert pack.metadata.license_id == "CC-BY-NC-SA-3.0"
    assert pack.metadata.sources[0].sha256
    assert pack.black[0].repr == "Prompt with ____ and {braces}."
    assert pack.black[0].template == "Prompt with {} and {{braces}}."
    assert pack.black[0].source_ref == "pyx-card:10;watermark:TST;draw:2;pick:1"
    assert pack.white[0].text == "A response\twith a tab."
    paths = export_all(packs, tmp_path)
    assert paths == (tmp_path / "pyx-1-test-set.carddeck",)
    with zipfile.ZipFile(paths[0]) as archive:
        assert "CC-BY-NC-SA-3.0" in archive.read("LICENSE.txt").decode()


def test_can_include_inactive_card_sets():
    packs = convert(SQL, source_url="https://example.test/pyx/cah_cards.sql", include_inactive=True)
    assert [pack.metadata.id for pack in packs] == ["pyx-1-test-set"]


def test_no_blank_prompt_appends_its_answer_placeholder():
    no_blank = SQL.replace(b"Prompt with ____ and {braces}.", b"What is the answer?")
    pack = convert(no_blank, source_url="https://example.test/pyx/cah_cards.sql")[0]
    assert pack.black[0].template == "What is the answer?\n{}"


def test_rejects_missing_provenance_or_unsupported_black_card():
    with pytest.raises(PackConfigurationError, match="source_url"):
        convert(SQL, source_url="")
    bad = SQL.replace(b"2\t1\tPrompt", b"2\t2\tPrompt")
    with pytest.raises(PackConfigurationError, match="pick/blank"):
        convert(bad, source_url="https://example.test/pyx/cah_cards.sql")
    missing = SQL.replace(b"id, draw, pick, text, watermark", b"id, pick, text, watermark").replace(
        b"10\t2\t1\tPrompt", b"10\t1\tPrompt"
    )
    with pytest.raises(PackConfigurationError, match="missing draw"):
        convert(missing, source_url="https://example.test/pyx/cah_cards.sql")


URL = "https://example.test/pyx/cah_cards.sql"


def _white_text(escaped: str, *, sql: bytes = SQL) -> str:
    edited = sql.replace(b"A response\\twith a tab.", escaped.encode("utf-8"))
    return convert(edited, source_url=URL)[0].white[0].text


@pytest.mark.parametrize(
    ("escaped", "expected"),
    [
        (r"end\101", "endA"),  # three-digit octal flush against the end of the value
        (r"\101nd", "And"),
        (r"a\7b", "a\x07b"),  # one-digit octal
        (r"a\12b", "a\nb"),  # two-digit octal
        (r"\1011", "A1"),  # octal takes at most three digits
        (r"\189", "\x01" "89"),  # 8 and 9 are not octal digits
        (r"\8\9", "89"),
        (r"\x41 and \x4a", "A and J"),
        (r"\x4", "\x04"),  # one-digit hex
        (r"\xZZ", "xZZ"),  # no hex digits: literal x
        (r"tail\x", "tailx"),
        (r"\x414", "A4"),  # hex takes at most two digits
        ("\\٣٣٣", "٣٣٣"),  # non-ASCII digits are never octal
    ],
)
def test_copy_escapes_octal_and_hex(escaped, expected):
    assert _white_text(escaped) == expected


@pytest.mark.parametrize(
    ("escaped", "expected"),
    [
        (r"\303\251", "\u00e9"),
        (r"\xc3\xa9", "\u00e9"),
        (r"\360\237\230\200", "\U0001f600"),
        (r"\xf0\x9f\x98\x80", "\U0001f600"),
        (r"caf\303\251 au lait", "caf\u00e9 au lait"),
        (r"\303\251\101\303\251", "\u00e9A\u00e9"),  # run, ASCII escape, run
    ],
)
def test_copy_byte_escapes_decode_as_utf8(escaped, expected):
    assert _white_text(escaped) == expected


@pytest.mark.parametrize(
    "escaped",
    [
        r"\351",  # lone Latin-1 byte
        r"\xe9",
        r"\360\237",  # truncated 4-byte sequence
        r"\303\n\251",  # a simple escape splits the run
        "\\303\u00e9",  # literal character after a lead byte
        r"\303\101",  # lead byte followed by an ASCII byte escape
        r"\251",  # bare continuation byte
    ],
)
def test_invalid_utf8_byte_escapes_are_rejected_as_configuration_errors(escaped):
    with pytest.raises(PackConfigurationError) as caught:
        _white_text(escaped)
    assert "invalid UTF-8 in COPY byte escapes" in str(caught.value)
    assert "SQL dump must be UTF-8" not in str(caught.value)


def test_out_of_range_octal_escape_is_rejected():
    with pytest.raises(PackConfigurationError, match="out of range"):
        _white_text(r"\777")


@pytest.mark.parametrize("separator", [" ", " ", "\u0085", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"])
def test_raw_unicode_line_separators_stay_inside_card_text(separator):
    assert _white_text(f"before{separator}after") == f"before{separator}after"


def test_crlf_dump_is_accepted():
    crlf = SQL.replace(b"\n", b"\r\n")
    pack = convert(crlf, source_url=URL)[0]
    assert pack.white[0].text == "A response\twith a tab."
    assert pack.black[0].repr == "Prompt with ____ and {braces}."


def test_only_one_trailing_carriage_return_is_stripped():
    doubled = SQL.replace(b"20\tA response\\twith a tab.\tTST\n", b"20\tA response\\twith a tab.\tTST\r\r\n")
    pack = convert(doubled, source_url=URL)[0]
    assert pack.white[0].source_ref == "pyx-card:20;watermark:TST\r"
