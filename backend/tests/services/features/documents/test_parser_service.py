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
    build_docx_alternate_content_text_box,
    build_docx_blocks,
    build_docx_nested_table,
    build_docx_table_with_text_box,
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


# --- DOCX tables and text boxes ------------------------------------------


def test_parse_docx_with_only_paragraphs_is_unchanged_by_block_walking() -> None:
    """Walking the body must not alter output for a plain prose document.

    Guards the backward-compatibility property the rest of the DOCX tests
    assume: a file with no tables and no shapes parses exactly as before.
    """

    blocks = build_docx_blocks(
        [
            ("heading", "Introduction"),
            ("paragraph", "First body paragraph."),
            ("paragraph", "Second body paragraph."),
        ]
    )

    parsed = parse_document(blocks, "prose.docx", DOCX_TYPE)

    assert [(s.section_title, s.text) for s in parsed.sections] == [
        ("Introduction", "First body paragraph.\nSecond body paragraph.")
    ]


def test_parse_docx_extracts_a_document_that_is_only_a_table() -> None:
    """A file whose entire content is tabular is no longer empty."""

    data = build_docx_blocks(
        [("table", [["Metric", "Value"], ["Revenue", "10"], ["Margin", "22%"]])]
    )

    parsed = parse_document(data, "figures.docx", DOCX_TYPE)

    assert [section.text for section in parsed.sections] == [
        "Metric: Revenue | Value: 10\nMetric: Margin | Value: 22%"
    ]


def test_parse_docx_extracts_a_document_that_is_only_text_boxes() -> None:
    """Shape-only layouts extracted as empty before; they now yield text."""

    data = build_docx_blocks([("textbox", "Core capability\nData governance")])

    parsed = parse_document(data, "capability.docx", DOCX_TYPE)

    assert [section.text for section in parsed.sections] == [
        "Core capability\nData governance"
    ]


def test_parse_docx_keeps_paragraphs_and_tables_in_document_order() -> None:
    """A table between two paragraphs stays between them.

    `document.paragraphs` and `document.tables` are separate flat lists, so
    reading them independently would move every table to the end.
    """

    data = build_docx_blocks(
        [
            ("paragraph", "Before the table."),
            ("table", [["Name", "Role"], ["Ann", "Lead"]]),
            ("paragraph", "After the table."),
        ]
    )

    parsed = parse_document(data, "ordered.docx", DOCX_TYPE)

    assert [section.text for section in parsed.sections] == [
        "Before the table.",
        "Name: Ann | Role: Lead",
        "After the table.",
    ]


def test_parse_docx_keeps_mixed_content_in_document_order() -> None:
    """Headings, prose, tables and shapes interleave in body order."""

    data = build_docx_blocks(
        [
            ("heading", "Overview"),
            ("paragraph", "Opening prose."),
            ("table", [["Name", "Role"], ["Ann", "Lead"]]),
            ("textbox", "Pull quote"),
            ("heading", "Detail"),
            ("paragraph", "Closing prose."),
        ]
    )

    parsed = parse_document(data, "mixed.docx", DOCX_TYPE)

    assert [(s.section_title, s.text) for s in parsed.sections] == [
        ("Overview", "Opening prose."),
        ("Overview", "Name: Ann | Role: Lead"),
        ("Overview", "Pull quote"),
        ("Detail", "Closing prose."),
    ]


def test_parse_docx_qualifies_each_table_row_with_its_header() -> None:
    """Every data row restates its column labels.

    A chunk boundary can fall between any two rows, so a row that has been
    separated from a header line would otherwise lose the meaning of its
    values.
    """

    data = build_docx_blocks(
        [
            (
                "table",
                [
                    ["Contract", "Agency", "Value"],
                    ["SF-DART", "DOT", "1.2M"],
                    ["SF-RAIL", "FTA", "800K"],
                ],
            )
        ]
    )

    parsed = parse_document(data, "contracts.docx", DOCX_TYPE)

    assert parsed.sections[0].text == (
        "Contract: SF-DART | Agency: DOT | Value: 1.2M\n"
        "Contract: SF-RAIL | Agency: FTA | Value: 800K"
    )


def test_parse_docx_keeps_a_table_out_of_the_surrounding_prose_section() -> None:
    """Table text becomes its own section so a chunk never straddles both."""

    data = build_docx_blocks(
        [
            ("paragraph", "Prose."),
            ("table", [["A", "B"], ["1", "2"]]),
        ]
    )

    parsed = parse_document(data, "split.docx", DOCX_TYPE)

    assert len(parsed.sections) == 2
    assert "A: 1" not in parsed.sections[0].text


def test_parse_docx_collapses_a_horizontally_merged_cell() -> None:
    """A merged cell is reported once, not once per column it spans."""

    data = build_docx_blocks([("merged", [["Name", "Role"], ["Ann", "Lead"]])])

    parsed = parse_document(data, "merged.docx", DOCX_TYPE)

    text = parsed.sections[0].text
    assert text.count("Name Role") == 1
    assert text.endswith("Ann | Lead")


def test_parse_docx_skips_entirely_empty_table_rows() -> None:
    """Spacer rows contribute no line rather than an empty one."""

    data = build_docx_blocks(
        [("table", [["Name", "Role"], ["", ""], ["Ann", "Lead"], ["", ""]])]
    )

    parsed = parse_document(data, "spaced.docx", DOCX_TYPE)

    assert parsed.sections[0].text == "Name: Ann | Role: Lead"


def test_parse_docx_falls_back_to_plain_rows_for_a_single_column_table() -> None:
    """A one-column layout table has no labels, so nothing is prefixed."""

    data = build_docx_blocks([("table", [["First line"], ["Second line"]])])

    parsed = parse_document(data, "layout.docx", DOCX_TYPE)

    assert parsed.sections[0].text == "First line\nSecond line"


def test_parse_docx_falls_back_to_plain_rows_when_headers_repeat() -> None:
    """Repeated first-row values are formatting, not column labels."""

    data = build_docx_blocks([("table", [["Cell", "Cell"], ["Left", "Right"]])])

    parsed = parse_document(data, "grid.docx", DOCX_TYPE)

    assert parsed.sections[0].text == "Cell | Cell\nLeft | Right"


def test_parse_docx_keeps_a_header_only_table() -> None:
    """A single-row table still contributes its text."""

    data = build_docx_blocks([("table", [["Alpha", "Beta"]])])

    parsed = parse_document(data, "header.docx", DOCX_TYPE)

    assert parsed.sections[0].text == "Alpha | Beta"


def test_parse_docx_reads_a_table_nested_inside_a_cell() -> None:
    """`_Cell.text` sees only direct paragraphs, so nesting is walked."""

    data = build_docx_nested_table("Outer label", [["k1", "v1"], ["k2", "v2"]])

    parsed = parse_document(data, "nested.docx", DOCX_TYPE)

    text = parsed.sections[0].text
    assert "Outer label" in text
    assert "k2" in text and "v2" in text


def test_parse_docx_reads_a_text_box_inside_a_table_cell() -> None:
    """Shapes anchored inside cells are part of that cell's text."""

    data = build_docx_table_with_text_box(
        [["Name", "Note"], ["Ann", "Plain"]], "Shape note"
    )

    parsed = parse_document(data, "cellbox.docx", DOCX_TYPE)

    assert "Shape note" in parsed.sections[0].text


def test_parse_docx_places_a_text_box_after_its_anchoring_paragraph() -> None:
    """A shape reads after the paragraph it is anchored to, not before."""

    data = build_docx_blocks([("paragraph", "Anchor text."), ("textbox", "Shape text")])

    parsed = parse_document(data, "anchor.docx", DOCX_TYPE)

    assert [section.text for section in parsed.sections] == [
        "Anchor text.",
        "Shape text",
    ]


def test_parse_docx_reads_a_dual_encoded_text_box_once() -> None:
    """Word's `mc:Choice` / `mc:Fallback` pair is one shape, not two.

    Regression: reading both branches doubled every shape in the corpus's
    capability statements.
    """

    data = build_docx_alternate_content_text_box("Core Competencies")

    parsed = parse_document(data, "shape.docx", DOCX_TYPE)

    assert [section.text for section in parsed.sections] == ["Core Competencies"]


def test_parse_docx_rejects_a_document_with_no_content() -> None:
    """An empty DOCX yields no sections and is rejected, not stored blank."""

    with pytest.raises(EmptyDocumentError):
        parse_document(build_docx_blocks([]), "empty.docx", DOCX_TYPE)


def test_parse_docx_rejects_a_document_of_only_empty_tables() -> None:
    """A table of blank cells is no more ingestible than a blank file."""

    data = build_docx_blocks([("table", [["", ""], ["", ""]])])

    with pytest.raises(EmptyDocumentError):
        parse_document(data, "blank-table.docx", DOCX_TYPE)


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
