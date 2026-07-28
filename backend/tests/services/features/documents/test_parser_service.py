import pytest

from app.core.exceptions import (
    DocumentParseError,
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
)
from app.services.features.documents.parser_service import (
    parse_document,
    resolve_format,
)
from tests.fixtures.factories import (
    build_docx,
    build_markdown,
    build_pdf,
    build_text,
)

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# --- format resolution ---------------------------------------------------


@pytest.mark.parametrize(
    "filename, content_type, expected",
    [
        ("report.pdf", PDF_TYPE, "pdf"),
        ("report.docx", DOCX_TYPE, "docx"),
        ("notes.txt", "text/plain", "txt"),
        ("notes.md", "text/markdown", "md"),
        ("notes.markdown", "text/x-markdown", "md"),
    ],
)
def test_resolve_format_uses_declared_content_type(
    filename: str, content_type: str, expected: str
) -> None:
    """A recognised content type determines the parser."""

    assert resolve_format(filename, content_type) == expected


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("report.pdf", "pdf"),
        ("report.PDF", "pdf"),
        ("report.docx", "docx"),
        ("notes.txt", "txt"),
        ("notes.md", "md"),
    ],
)
def test_resolve_format_falls_back_to_extension(filename: str, expected: str) -> None:
    """Clients sending a generic content type still resolve by extension."""

    assert resolve_format(filename, "application/octet-stream") == expected


def test_resolve_format_ignores_content_type_parameters() -> None:
    """A charset parameter does not defeat content-type matching."""

    assert resolve_format("notes.txt", "text/plain; charset=utf-8") == "txt"


@pytest.mark.parametrize(
    "filename, content_type",
    [
        ("archive.zip", "application/zip"),
        ("image.png", "image/png"),
        ("sheet.xlsx", "application/vnd.ms-excel"),
        ("noextension", None),
    ],
)
def test_resolve_format_rejects_unsupported_types(
    filename: str, content_type: str | None
) -> None:
    """Unsupported formats raise rather than being parsed as text."""

    with pytest.raises(UnsupportedDocumentTypeError):
        resolve_format(filename, content_type)


# --- PDF -----------------------------------------------------------------


def test_parse_pdf_returns_one_section_per_page_with_page_numbers() -> None:
    """PDF text is extracted per page, with 1-indexed page numbers."""

    pdf = build_pdf(
        ["Alpha page one content", "Beta page two content", "Gamma page three"]
    )

    parsed = parse_document(pdf, "report.pdf", PDF_TYPE)

    assert parsed.page_count == 3
    assert [section.page_number for section in parsed.sections] == [1, 2, 3]
    assert "Alpha" in parsed.sections[0].text
    assert "Gamma" in parsed.sections[2].text


def test_parse_pdf_skips_pages_with_no_extractable_text() -> None:
    """Blank pages are omitted, but still counted in page_count."""

    pdf = build_pdf(["Only this page has words", "", "Also this one"])

    parsed = parse_document(pdf, "report.pdf", PDF_TYPE)

    assert parsed.page_count == 3
    assert [section.page_number for section in parsed.sections] == [1, 3]


def test_parse_pdf_raises_when_no_page_has_text() -> None:
    """A PDF with no text layer is rejected rather than silently ingested."""

    pdf = build_pdf(["", ""])

    with pytest.raises(EmptyDocumentError):
        parse_document(pdf, "scanned.pdf", PDF_TYPE)


def test_parse_pdf_raises_document_parse_error_for_corrupt_bytes() -> None:
    """Bytes claiming to be a PDF but which are not raise a parse error."""

    with pytest.raises(DocumentParseError):
        parse_document(b"this is definitely not a pdf", "broken.pdf", PDF_TYPE)


# --- DOCX ----------------------------------------------------------------


def test_parse_docx_groups_paragraphs_under_their_heading() -> None:
    """Each heading starts a new section carrying that heading as its title."""

    data = build_docx(
        [
            ("Introduction", "This is the introduction body."),
            ("Methods", "This is the methods body."),
        ]
    )

    parsed = parse_document(data, "paper.docx", DOCX_TYPE)

    assert [section.section_title for section in parsed.sections] == [
        "Introduction",
        "Methods",
    ]
    assert "introduction body" in parsed.sections[0].text
    assert parsed.page_count is None


def test_parse_docx_reports_no_page_numbers() -> None:
    """DOCX has no reliable pagination, so page_number stays unset."""

    data = build_docx([("Title", "Body")])

    parsed = parse_document(data, "paper.docx", DOCX_TYPE)

    assert all(section.page_number is None for section in parsed.sections)


def test_parse_docx_raises_document_parse_error_for_corrupt_bytes() -> None:
    """Unreadable DOCX bytes surface as a parse error."""

    with pytest.raises(DocumentParseError):
        parse_document(b"not a real docx", "broken.docx", DOCX_TYPE)


# --- Markdown ------------------------------------------------------------


def test_parse_markdown_splits_on_headings() -> None:
    """ATX headings become section titles and start new sections."""

    data = build_markdown(
        [("Overview", "Overview body text."), ("Details", "Details body text.")]
    )

    parsed = parse_document(data, "notes.md", "text/markdown")

    assert [section.section_title for section in parsed.sections] == [
        "Overview",
        "Details",
    ]
    assert "Overview body text." in parsed.sections[0].text


def test_parse_markdown_keeps_content_before_the_first_heading() -> None:
    """A preamble with no heading is preserved with no section title."""

    data = b"Preamble paragraph.\n\n# First Heading\n\nSection body."

    parsed = parse_document(data, "notes.md", "text/markdown")

    assert parsed.sections[0].section_title is None
    assert "Preamble paragraph." in parsed.sections[0].text
    assert parsed.sections[1].section_title == "First Heading"


# --- Plain text ----------------------------------------------------------


def test_parse_text_returns_a_single_untitled_section() -> None:
    """Plain text has no structure to preserve."""

    parsed = parse_document(build_text("Line one\nLine two"), "n.txt", "text/plain")

    assert len(parsed.sections) == 1
    assert parsed.sections[0].section_title is None
    assert parsed.sections[0].page_number is None
    assert parsed.sections[0].text == "Line one\nLine two"


def test_parse_text_decodes_non_utf8_bytes() -> None:
    """Windows-encoded text is decoded rather than rejected."""

    parsed = parse_document("Café notes".encode("cp1252"), "notes.txt", "text/plain")

    assert "notes" in parsed.sections[0].text


def test_parse_text_strips_a_utf8_byte_order_mark() -> None:
    """A BOM does not leak into the extracted text."""

    parsed = parse_document(b"\xef\xbb\xbfHello", "notes.txt", "text/plain")

    assert parsed.sections[0].text == "Hello"


# --- Empty input ---------------------------------------------------------


def test_parse_document_rejects_empty_bytes() -> None:
    """A zero-byte upload is rejected before format resolution."""

    with pytest.raises(EmptyDocumentError):
        parse_document(b"", "notes.txt", "text/plain")


@pytest.mark.parametrize("body", ["", "   ", "\n\n\t "])
def test_parse_document_rejects_whitespace_only_text(body: str) -> None:
    """Whitespace-only content yields no sections and is rejected."""

    with pytest.raises(EmptyDocumentError):
        parse_document(build_text(body) or b" ", "notes.txt", "text/plain")


def test_parsed_document_text_joins_all_sections() -> None:
    """The convenience `text` property concatenates sections in order."""

    parsed = parse_document(
        build_markdown([("A", "first"), ("B", "second")]),
        "notes.md",
        "text/markdown",
    )

    assert parsed.text == "first\n\nsecond"
