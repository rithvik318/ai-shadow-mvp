"""Encoding detection and control-character hygiene, as one concern.

They are one concern because one bug produced both symptoms. A UTF-16 HTML
export in the SunRadia knowledge base was decoded by `cp1252` — which raises
for almost nothing — into a string where every second character was U+0000.
PostgreSQL cannot store U+0000 at all, so psycopg2 refused the parameter with
`ValueError: A string literal cannot contain NUL (0x00) characters`, an error
from outside the document hierarchy, which surfaced as the useless
"Ingestion failed unexpectedly (ValueError)".

Three files failed that way. Two were UTF-16 exports; the third,
`Summary View.pdf`, was an ordinary readable six-page PDF whose font maps a
handful of glyphs to U+0000 — no encoding problem at all, just thirteen stray
NULs. So the fix is in two parts, and so is this module: decode properly, and
never let a control character reach the database regardless of where it came
from.

SQLite accepts NULs without complaint, which is why the whole suite passed
throughout. These tests assert on the extracted string rather than on a
database, so they hold in both dialects — and `tests/database/` covers the
PostgreSQL side.
"""

import pytest

from app.core.exceptions import DocumentParseError, NoTextLayerError
from app.services.features.documents.parser_service import (
    _decode,
    parse_document,
)
from tests.fixtures.factories import build_html, build_text, build_utf16_html

PAGE = [("Functional Processes", "Initiate, then update and maintain metadata.")]


def _text(data: bytes, name: str, content_type: str) -> str:
    return parse_document(data, name, content_type).text


class TestDecodingFollowsTheByteOrderMark:
    def test_a_utf16_file_is_read_as_text_not_as_alternating_nulls(self) -> None:
        """The whole defect in one assertion."""

        extracted = _text(build_utf16_html(PAGE), "export.html", "text/html")

        assert "Initiate, then update and maintain metadata." in extracted
        assert "\x00" not in extracted

    def test_big_endian_is_read_too(self) -> None:
        extracted = _text(
            build_utf16_html(PAGE, big_endian=True), "export.html", "text/html"
        )

        assert "Initiate, then update and maintain metadata." in extracted

    def test_a_utf16_file_with_no_byte_order_mark_is_still_recognised(self) -> None:
        """Nothing declares the encoding, so the NUL pattern has to.

        A single-byte codec would accept these bytes silently, so failing to
        recognise them here is not a decode error — it is a wrong answer.
        """

        extracted = _text(build_utf16_html(PAGE, bom=False), "export.html", "text/html")

        assert "Initiate, then update and maintain metadata." in extracted
        assert "\x00" not in extracted

    def test_a_utf8_file_with_a_byte_order_mark_keeps_working(self) -> None:
        assert _decode("﻿Plain text.".encode()) == "Plain text."

    def test_ordinary_utf8_is_unaffected(self) -> None:
        assert _decode("Ordinary — with an em dash.".encode()) == (
            "Ordinary — with an em dash."
        )

    def test_windows_text_still_falls_back_to_cp1252(self) -> None:
        """The fallback chain is narrowed by the BOM check, not replaced."""

        assert _decode("Fée".encode("cp1252")) == "Fée"

    def test_a_byte_order_mark_that_lies_is_an_error_rather_than_mojibake(
        self,
    ) -> None:
        """A declared UTF-16 file with an odd byte count cannot be what it says.

        Reported as a parse failure rather than quietly re-guessed: a file
        whose own header is wrong is a broken file, and guessing past it is how
        the original bug produced plausible-looking nonsense.
        """

        with pytest.raises(DocumentParseError):
            _decode(b"\xff\xfe\x41")

    def test_a_file_too_short_to_classify_is_not_mistaken_for_utf16(self) -> None:
        assert _decode(b"Hi") == "Hi"


class TestControlCharactersNeverReachTheDatabase:
    def test_a_nul_inside_otherwise_good_text_is_stripped(self) -> None:
        """`Summary View.pdf` in miniature: a readable document, 13 stray NULs.

        Nothing about this file is malformed. PostgreSQL simply will not hold
        the character, so it cannot be allowed past the parser.
        """

        extracted = _text(build_text("Supplier\x00 Data"), "summary.txt", "text/plain")

        assert "\x00" not in extracted
        assert "Supplier Data" in extracted

    def test_other_control_characters_go_too(self) -> None:
        extracted = _text(
            build_text("Before\x01\x02\x1b\x7f\x9dAfter"), "n.txt", "text/plain"
        )

        assert extracted == "BeforeAfter"

    @pytest.mark.parametrize("whitespace", ["\t", "\n", "\r"])
    def test_layout_whitespace_is_kept(self, whitespace: str) -> None:
        """Chunking and citation are built on these; they are not noise."""

        extracted = _text(build_text(f"Left{whitespace}Right"), "n.txt", "text/plain")

        assert whitespace in extracted

    def test_a_section_left_empty_by_stripping_is_dropped_not_blanked(self) -> None:
        extracted = parse_document(
            build_html([("Heading", "Real prose."), ("\x00\x01", "\x00")]),
            "page.html",
            "text/html",
        )

        assert all(section.text.strip() for section in extracted.sections)

    def test_a_document_that_is_only_control_characters_needs_ocr(self) -> None:
        """Stripping runs before the emptiness check, deliberately.

        The alternative is a row of sections that are empty strings, which is
        neither a failure anybody can act on nor an honest description of the
        file.
        """

        with pytest.raises(NoTextLayerError):
            parse_document(build_text("\x00\x00\x01"), "scan.txt", "text/plain")

    def test_an_unprintable_heading_does_not_become_an_empty_title(self) -> None:
        parsed = parse_document(
            build_html([("Good\x00 Heading", "Prose.")]), "page.html", "text/html"
        )

        titles = [s.section_title for s in parsed.sections if s.section_title]

        assert all("\x00" not in title for title in titles)
