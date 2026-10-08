"""Extraction of plain text and provenance metadata from uploaded files.

Pure functions over bytes: no database access, no configuration, no network.
That keeps the format-specific logic — which is where the awkward edge cases
live — independently testable.
"""

import codecs
import io
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import docx
import openpyxl
import pypdf
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.core.constants import (
    KNOWN_UNSUPPORTED_EXTENSIONS,
    NO_TEXT_LAYER_REASON,
    SUPPORTED_CONTENT_TYPES,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_FORMAT_SUMMARY,
)
from app.core.exceptions import (
    DocumentParseError,
    EmptyDocumentError,
    NoTextLayerError,
    UnsupportedDocumentTypeError,
)

logger = logging.getLogger(__name__)

_MARKDOWN_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*$")
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# Resolved once: `qn` builds a Clark-notation tag on every call.
_TEXT_BOX_TAG = qn("w:txbxContent")
_PARAGRAPH_TAG = qn("w:p")
_TEXT_TAG = qn("w:t")

# Markup Compatibility, which `python-docx` does not register a prefix for.
_FALLBACK_TAG = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

# Subtrees whose text a paragraph must not claim. Text boxes and nested tables
# are emitted by their own handlers, so taking them here as well would repeat
# them; a deletion is not content.
_NOT_PARAGRAPH_TEXT = frozenset(
    {_TEXT_BOX_TAG, _FALLBACK_TAG, qn("w:tbl"), qn("w:del")}
)

_RUN_TAG = qn("w:r")

# `section_title` is a display and citation label, never embedded and never
# searched, so it is bounded — while the heading it came from is kept whole in
# the section's own text. The bound also keeps a heading out of the chunk
# column it would overflow: `DocumentChunk.section_title` is `String(512)`,
# which PostgreSQL enforces and SQLite does not, so an over-long heading would
# fail an ingest that every test passes. 200 leaves 322 of the 323 titles in
# the SunRadia corpus untouched — the median is 28 and the 99th percentile 91.
MAX_SECTION_TITLE_LENGTH = 200


# A header cell is a label. Authors also build page layouts out of tables, and
# a first row holding paragraphs of prose is a layout, not a header — reading
# it as one attaches the prose to the rows below as a prefix and drops any
# column with no cell beneath it. Measured across the corpus's 90 header-like
# DOCX tables, 87 have a longest header cell of 64 characters or less and the
# longest genuine one is 64; the next three are 137, 191 and 2266, and all
# three are prose laid out in a grid. 200 sits in that gap.
_MAX_HEADER_CELL_LENGTH = 200


def _bounded_title(title: str | None) -> str | None:
    """Return a heading trimmed to the length a title column can hold."""

    if title is None:
        return None

    return title[:MAX_SECTION_TITLE_LENGTH]


# What a run's non-text children stand for, matching `Run.text`. Without these
# a tab between two words closes up and welds them into one token.
_RUN_SEPARATORS = {qn("w:tab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n"}


@dataclass(frozen=True)
class ParsedSection:
    """A slice of extracted text together with where it came from.

    `page_number` is 1-indexed and only available for paginated formats.
    `section_title` is the nearest preceding heading, where the format exposes
    one.
    """

    text: str
    page_number: int | None = None
    section_title: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """The full result of parsing one uploaded file."""

    sections: list[ParsedSection]
    page_count: int | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(section.text for section in self.sections)


def resolve_format(filename: str, content_type: str | None) -> str:
    """Return the short format key for an upload, or raise.

    The declared content type is trusted first, since browsers set it
    reliably for these formats; the extension is a fallback for clients that
    send `application/octet-stream`.
    """

    normalized_type = (content_type or "").split(";")[0].strip().lower()
    if normalized_type in SUPPORTED_CONTENT_TYPES:
        return SUPPORTED_CONTENT_TYPES[normalized_type]

    extension = Path(filename).suffix.lower()
    if extension in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[extension]

    # A format we recognise and deliberately do not parse gets its own answer.
    # "Unsupported document type: .doc" tells somebody nothing they can act on;
    # "save it as .docx" is the entire remedy, and across a real corpus this is
    # the difference between a list of rejects and a list of tasks.
    if extension in KNOWN_UNSUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentTypeError(
            f"{KNOWN_UNSUPPORTED_EXTENSIONS[extension]}, and it will be indexed "
            "on the next synchronisation."
        )

    raise UnsupportedDocumentTypeError(
        f"Unsupported document type: {content_type or extension or 'unknown'}. "
        f"Supported formats are {SUPPORTED_FORMAT_SUMMARY}."
    )


#: Byte-order marks, longest first. Order is load-bearing: UTF-32LE's BOM
#: *begins with* UTF-16LE's, so testing the short one first would decode a
#: UTF-32 file as UTF-16 and never notice.
_BYTE_ORDER_MARKS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _wide_encoding(data: bytes) -> str | None:
    """Name the UTF-16 variant of a file that declares no byte-order mark.

    Latin text in UTF-16 is half NUL bytes, and which half says the endianness.
    The test is deliberately blunt because the alternative is worse: every
    single-byte codec in the fallback chain accepts those NULs happily, so an
    undetected UTF-16 file is not a decode failure — it is a silent success
    that yields a string of alternating characters and NULs.
    """

    sample = data[:4096]

    if len(sample) < 4:
        return None

    even = sample[0::2].count(0)
    odd = sample[1::2].count(0)
    half = len(sample) // 2

    # A third of one side NUL and almost none of the other. Real single-byte
    # text has no NULs at all, so anything near this ratio is a wide encoding.
    if odd > half * 0.3 and even < half * 0.05:
        return "utf-16-le"

    if even > half * 0.3 and odd < half * 0.05:
        return "utf-16-be"

    return None


def _decode(data: bytes) -> str:
    """Decode a text file, honouring its byte-order mark before guessing.

    The BOM check exists because guessing cannot fail safely. `cp1252` and
    `latin-1` map almost every byte, so a UTF-16 file decodes *without raising*
    into mojibake in which every second character is U+0000 — and PostgreSQL
    cannot store U+0000 at all. A MindManager HTML export in the SunRadia
    corpus did exactly this: `\\xff\\xfe` was read as `ÿþ`, the markup arrived
    with 19,741 NULs in it, and psycopg2 ended the ingest with
    `ValueError: A string literal cannot contain NUL (0x00) characters.`
    """

    for mark, encoding in _BYTE_ORDER_MARKS:
        if data.startswith(mark):
            try:
                # `utf-16`/`utf-32` without a suffix consume the BOM and take
                # the endianness from it, which is exactly what is wanted.
                return data.decode(encoding)
            except UnicodeDecodeError as exc:
                raise DocumentParseError(
                    f"File declares a {encoding} byte-order mark but does not "
                    "decode as it."
                ) from exc

    wide = _wide_encoding(data)

    if wide is not None:
        try:
            return data.decode(wide)
        except UnicodeDecodeError:
            pass

    for encoding in _TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise DocumentParseError("File could not be decoded as text.")


def _parse_pdf(data: bytes) -> ParsedDocument:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        page_count = len(reader.pages)
        sections = [
            ParsedSection(text=text.strip(), page_number=number)
            for number, page in enumerate(reader.pages, start=1)
            if (text := page.extract_text() or "").strip()
        ]
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError(f"PDF could not be read: {exc}") from exc

    return ParsedDocument(sections=sections, page_count=page_count)


def _iter_body_blocks(document: docx.document.Document):
    """Yield paragraphs and tables in the order they appear in the document.

    `document.paragraphs` and `document.tables` are two flat lists with no
    interleaving, so reading them separately would append every table after
    every paragraph. Walking the body's own children is the only way to keep a
    table sitting between the paragraphs it belongs to.
    """

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _text_box_blocks(element) -> list[str]:
    """Return the text of every text box anchored inside `element`.

    Text box content lives in `w:txbxContent`, nested under `w:drawing`
    (DrawingML) or `w:pict` (legacy VML). Either way the marker element is the
    same, so one search catches both. `python-docx` does not surface this at
    all: `Paragraph.text` reads `w:t` from direct runs, and a text box's runs
    sit several levels deeper — which is why documents whose content is laid
    out entirely in shapes currently extract as empty.

    Word usually writes both encodings of the same shape, wrapped in an
    `mc:AlternateContent` pair: `mc:Choice` holds the DrawingML a modern
    reader uses, `mc:Fallback` the VML an old one does. They carry identical
    text, so the fallback is skipped or every shape would be read twice.
    """

    blocks: list[str] = []

    # `iter` with qualified tags rather than XPath: `w:txbxContent` has no
    # registered `python-docx` element class, so it comes back as a plain lxml
    # element whose `.xpath()` has no namespace map — while its `w:p` children
    # do have one and reject a `namespaces=` argument. `iter` sidesteps the
    # inconsistency and skips the XPath engine entirely.
    for box in element.iter(_TEXT_BOX_TAG):
        # A text box inside another is already covered by the outer one's
        # descendant walk; taking both would duplicate its text.
        if any(
            ancestor.tag in (_TEXT_BOX_TAG, _FALLBACK_TAG)
            for ancestor in box.iterancestors()
        ):
            continue

        lines = [
            line
            for paragraph in box.iter(_PARAGRAPH_TAG)
            if (
                line := "".join(
                    node.text or "" for node in paragraph.iter(_TEXT_TAG)
                ).strip()
            )
        ]

        if body := "\n".join(lines).strip():
            blocks.append(body)

    return blocks


def _paragraph_text(paragraph: Paragraph) -> str:
    """Return a paragraph's text, including runs Word nests inside wrappers.

    `Paragraph.text` concatenates the paragraph's direct `w:r` children (and,
    since python-docx 1.1, its hyperlinks). A run wrapped in anything else is
    invisible to it: a tracked insertion (`w:ins`), a field result
    (`w:fldSimple`), a smart tag, a content control. In the SunRadia corpus
    that hid the whole body of a document whose changes were never accepted —
    570 paragraphs, of which `Paragraph.text` saw 17 — and the parse still
    reported success, so the document indexed as an empty shell.

    Reading `w:t` descendants instead catches every such wrapper without
    enumerating them, which matters because the list is open-ended. Deleted
    text is excluded for free: Word stores it as `w:delText`, a different tag.
    """

    element = paragraph._p
    parts: list[str] = []

    for node in element.iter():
        if node.tag == _TEXT_TAG:
            value = node.text or ""
        elif node.tag in _RUN_SEPARATORS and node.getparent().tag == _RUN_TAG:
            # Only inside a run: `w:tab` also appears in `w:pPr` as a tab-stop
            # definition, which is layout rather than content.
            value = _RUN_SEPARATORS[node.tag]
        else:
            continue

        ancestor = node.getparent()

        while ancestor is not None and ancestor is not element:
            if ancestor.tag in _NOT_PARAGRAPH_TEXT:
                break

            ancestor = ancestor.getparent()
        else:
            parts.append(value)

    # Joined without a separator, exactly as `Paragraph.text` joins runs: Word
    # splits a single word across runs freely, so anything else inserts breaks
    # mid-word.
    return "".join(parts)


def _cell_text(cell: _Cell) -> str:
    """Flatten one table cell to a single line.

    Includes nested tables and text boxes, both of which `_Cell.text` skips —
    it reads only the cell's direct paragraphs.
    """

    direct = " ".join(_paragraph_text(paragraph) for paragraph in cell.paragraphs)
    parts = [" ".join(direct.split())]
    parts.extend(_text_box_blocks(cell._tc))
    parts.extend(
        " ".join(_cell_text(inner) for inner in row.cells)
        for nested in cell.tables
        for row in nested.rows
    )

    return " ".join(part for part in parts if part.strip()).strip()


def _table_rows(table: Table) -> list[list[str]]:
    """Return non-empty rows as lists of cell text, merged cells collapsed."""

    rows: list[list[str]] = []

    for row in table.rows:
        cells: list[str] = []
        seen: set[int] = set()

        for cell in row.cells:
            # A horizontally merged cell is returned once per column it spans,
            # backed by the same `w:tc`. Emitting it repeatedly would restate
            # its value across several columns.
            if id(cell._tc) in seen:
                continue

            seen.add(id(cell._tc))
            cells.append(_cell_text(cell))

        if any(cells):
            rows.append(cells)

    return rows


def _serialise_rows(rows: list[list[str]]) -> str:
    """Render already-extracted table rows as one `Header: value` line per row.

    Chosen over a Markdown grid because a chunk boundary can fall anywhere:
    a grid row separated from its header line becomes unreadable, whereas a
    header-qualified row still says what each value means. It also embeds
    closer to prose than pipe-delimited columns, and reads naturally when the
    model receives it as context.

    Tables with no usable header — a single row, a single column, a first row
    that does not look like labels, or one whose cells are too long to be
    labels at all — fall back to plain delimited rows, which is the right
    shape for the layout tables authors use for formatting rather than data.

    Takes rows rather than a table object so DOCX and PPTX tables reach the
    reader in one format. Only the extraction of cell text differs between
    them; how a table reads should not.
    """

    if not rows:
        return ""

    header = rows[0]
    labels = [cell for cell in header if cell]
    has_header = (
        len(rows) > 1
        and len(labels) >= 2
        and len(set(labels)) == len(labels)
        and all(len(label) <= _MAX_HEADER_CELL_LENGTH for label in labels)
    )

    if not has_header:
        return "\n".join(" | ".join(cell for cell in row if cell) for row in rows)

    lines: list[str] = []

    for row in rows[1:]:
        pairs = []
        for position, value in enumerate(row):
            if not value:
                continue

            label = header[position] if position < len(header) else ""
            pairs.append(f"{label}: {value}" if label else value)

        if pairs:
            lines.append(" | ".join(pairs))

    # A header with no data rows beneath it is still worth keeping.
    return "\n".join(lines) if lines else " | ".join(labels)


def _serialise_table(table: Table) -> str:
    """Render one DOCX table."""

    try:
        rows = _table_rows(table)
    except Exception:
        # Malformed grids (bad gridSpan, truncated rows) should cost their
        # own content, not the whole document.
        return ""

    return _serialise_rows(rows)


def _parse_docx(data: bytes) -> ParsedDocument:
    """Extract DOCX paragraphs, tables and text boxes, in document order.

    DOCX has no reliable page boundaries without rendering the document, so
    `page_number` stays None and headings carry the provenance instead.

    Tables and text boxes each become their own section so that a chunk never
    mixes a table row with surrounding prose — the same invariant the page and
    heading splits already maintain.
    """

    try:
        document = docx.Document(io.BytesIO(data))
        blocks = list(_iter_body_blocks(document))
    except Exception as exc:
        raise DocumentParseError(f"DOCX could not be read: {exc}") from exc

    sections: list[ParsedSection] = []
    current_title: str | None = None
    buffer: list[str] = []
    title_carried = False

    def flush() -> None:
        nonlocal title_carried

        body = "\n".join(buffer).strip()
        if body:
            sections.append(
                ParsedSection(text=body, section_title=_bounded_title(current_title))
            )
            title_carried = True
        buffer.clear()

    def emit(text: str) -> None:
        nonlocal title_carried

        flush()
        sections.append(
            ParsedSection(text=text, section_title=_bounded_title(current_title))
        )
        title_carried = True

    def close_title() -> None:
        """Finish the current heading before it is replaced or the file ends.

        A heading only survives as the `section_title` of a section emitted
        after it. Left alone, a heading followed straight by another heading —
        or one ending the document — is overwritten and its text is gone, with
        nothing failing to show for it. Where that would happen the heading
        becomes its own section instead, which is also what the PPTX parser
        does with a slide title that has no other content beside it.

        Only when nothing else carried it: a heading above a table or a text
        box is already that section's title, and repeating it would restate
        the same words twice.
        """

        nonlocal title_carried

        flush()

        if current_title is not None and not title_carried:
            sections.append(
                ParsedSection(
                    text=current_title, section_title=_bounded_title(current_title)
                )
            )
            title_carried = True

    for block in blocks:
        if isinstance(block, Table):
            if serialised := _serialise_table(block):
                emit(serialised)
            continue

        text = _paragraph_text(block).strip()

        if text:
            style_name = (block.style.name or "") if block.style else ""
            if style_name.lower().startswith("heading") or style_name == "Title":
                close_title()
                current_title = text
                title_carried = False

                # A heading too long to fit the label has to become content in
                # its own right, or bounding the label would be the thing that
                # loses it. Authors style whole paragraphs as headings, and
                # the corpus's longest is 636 characters of contract prose.
                if len(text) > MAX_SECTION_TITLE_LENGTH:
                    sections.append(
                        ParsedSection(text=text, section_title=_bounded_title(text))
                    )
                    title_carried = True
            else:
                buffer.append(text)

        # Runs after the paragraph's own text, so a shape anchored to a
        # paragraph lands after it rather than before.
        for box in _text_box_blocks(block._p):
            emit(box)

    close_title()
    return ParsedDocument(sections=sections, page_count=None)


# Sorts a shape whose position cannot be resolved to the end of the slide,
# where the stable sort leaves it in the order PowerPoint stored it.
_UNPOSITIONED = float("inf")


def _shape_position(shape) -> tuple[float, float]:
    """Top-left of a shape in EMU, for reading-order sorting."""

    try:
        top, left = shape.top, shape.left
    except Exception:
        return (_UNPOSITIONED, _UNPOSITIONED)

    return (
        _UNPOSITIONED if top is None else float(top),
        _UNPOSITIONED if left is None else float(left),
    )


def _ordered_shapes(shapes) -> list:
    """Return shapes in reading order: top to bottom, then left to right.

    A shape collection iterates in z-order — the order shapes were added to
    the slide, which has no relation to where they sit on it. A caption added
    last can be the topmost thing on the page. Sorting by position recovers the
    order a reader would use. The sort is stable, so shapes at the same
    position, and any whose position cannot be resolved, keep z-order rather
    than being reordered arbitrarily between runs.
    """

    return sorted(shapes, key=_shape_position)


def _pptx_table_rows(table) -> list[list[str]]:
    """Return non-empty rows as lists of cell text, merged cells collapsed.

    A merged region reports its text on the origin cell and reports every cell
    it spans as `is_spanned`, so skipping those emits the value once instead of
    once per covered column.
    """

    rows: list[list[str]] = []

    for row in table.rows:
        cells = [
            " ".join(cell.text.split()) for cell in row.cells if not cell.is_spanned
        ]

        if any(cells):
            rows.append(cells)

    return rows


def _shape_text(shape) -> str:
    """Return the text of one shape, blank paragraphs dropped."""

    if not shape.has_text_frame:
        return ""

    lines = [
        line
        for paragraph in shape.text_frame.paragraphs
        if (line := paragraph.text.strip())
    ]

    return "\n".join(lines)


def _shape_blocks(shape) -> list[str]:
    """Return the text blocks one shape contributes, recursing into groups.

    Pictures, connectors and other shapes with neither a text frame nor a table
    contribute nothing, which is what keeps decorative furniture out of the
    extracted text without needing to enumerate the decorative types.
    """

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        return [
            block
            for child in _ordered_shapes(shape.shapes)
            for block in _shape_blocks(child)
        ]

    if shape.has_table:
        return (
            [serialised]
            if (serialised := _serialise_rows(_pptx_table_rows(shape.table)))
            else []
        )

    return [text] if (text := _shape_text(shape)) else []


def _slide_notes(slide) -> str:
    """Return the speaker notes for a slide, or empty when it has none.

    Guarded by `has_notes_slide` because reading `notes_slide` creates one as a
    side effect. The notes slide also carries a thumbnail placeholder, so the
    text frame is addressed directly rather than walked as shapes.
    """

    if not slide.has_notes_slide:
        return ""

    frame = slide.notes_slide.notes_text_frame

    if frame is None:
        return ""

    return "\n".join(
        line for paragraph in frame.paragraphs if (line := paragraph.text.strip())
    )


def _slide_blocks(slide) -> tuple[str | None, list[str]]:
    """Return one slide's title and its text blocks, in reading order."""

    title_shape = slide.shapes.title
    title = " ".join(_shape_text(title_shape).split()) if title_shape else ""

    # Compared by id, not by identity: `python-pptx` builds a fresh proxy
    # object on every access, so `shapes.title` is never the same object as the
    # matching shape from iterating `shapes`, and an identity test would emit
    # the title twice.
    title_id = title_shape.shape_id if title_shape else None

    blocks: list[str] = []

    # The title leads regardless of geometry, and is repeated into the text
    # rather than only becoming `section_title`. A DOCX heading introduces the
    # sections that follow it, but a slide title is part of the one section the
    # slide becomes — and on a section-divider slide it is the only content
    # there is, so dropping it would lose the slide entirely.
    if title:
        blocks.append(title)

    for shape in _ordered_shapes(
        shape for shape in slide.shapes if shape.shape_id != title_id
    ):
        blocks.extend(_shape_blocks(shape))

    # Last, and labelled: notes are what the presenter said rather than what
    # the audience saw, and a reader of the retrieved context should be able to
    # tell the difference.
    if notes := _slide_notes(slide):
        blocks.append(f"Speaker notes: {notes}")

    return (title or None), blocks


def _parse_pptx(data: bytes) -> ParsedDocument:
    """Extract one section per slide, in slide order.

    `page_number` carries the 1-indexed slide ordinal — the same field PDF uses
    for its page, since both answer "where in the file did this come from".
    Citations therefore say "page 7" for slide 7 until the metadata work in
    docs/ROADMAP.md gives the locator a name.

    A slide that cannot be read is skipped with a warning rather than failing
    the presentation: one malformed shape tree should not cost the other
    ninety-nine slides.
    """

    try:
        presentation = Presentation(io.BytesIO(data))
        slides = list(presentation.slides)
    except Exception as exc:
        raise DocumentParseError(f"PPTX could not be read: {exc}") from exc

    sections: list[ParsedSection] = []

    for number, slide in enumerate(slides, start=1):
        try:
            title, blocks = _slide_blocks(slide)
        except Exception:
            logger.warning(
                "pptx_slide_skipped",
                extra={"slide_number": number, "slide_count": len(slides)},
                exc_info=True,
            )
            continue

        if text := "\n".join(blocks).strip():
            sections.append(
                ParsedSection(
                    text=text,
                    page_number=number,
                    section_title=_bounded_title(title),
                )
            )

    return ParsedDocument(sections=sections, page_count=len(slides))


def _parse_markdown(data: bytes) -> ParsedDocument:
    """Split Markdown on ATX headings so each section keeps its title."""

    content = _decode(data)
    sections: list[ParsedSection] = []
    current_title: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(ParsedSection(text=body, section_title=current_title))
        buffer.clear()

    for line in content.splitlines():
        match = _MARKDOWN_HEADING.match(line.strip())
        if match:
            flush()
            current_title = match.group("title").strip()
            continue

        buffer.append(line)

    flush()
    return ParsedDocument(sections=sections, page_count=None)


def _parse_text(data: bytes) -> ParsedDocument:
    content = _decode(data).strip()
    sections = [ParsedSection(text=content)] if content else []
    return ParsedDocument(sections=sections, page_count=None)


class _HtmlReader(HTMLParser):
    """Text and headings out of HTML, using the standard library only.

    A parser rather than a regular expression, because tag soup is exactly
    where a regular expression produces plausible-looking wrong answers. Not
    BeautifulSoup, because the whole job is "collect text, notice headings",
    and this repository has already once regretted taking a dependency to avoid
    thirty lines.

    Two things it deliberately drops. `script` and `style` hold source code, not
    prose, and indexing a page's JavaScript makes every page look similar to
    every other. `<br>` and block boundaries become whitespace, so words on
    either side of a tag do not run together — `a<br>b` is two words.
    """

    _SKIP = frozenset({"script", "style", "head", "title", "noscript"})
    _HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    _BLOCKS = frozenset(
        {"p", "div", "li", "tr", "br", "section", "article", "table", "blockquote"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        self.sections: list[ParsedSection] = []
        self._title: str | None = None
        self._buffer: list[str] = []
        self._heading: list[str] | None = None
        self._skipping = 0

    def _flush(self) -> None:
        body = " ".join(" ".join(self._buffer).split())
        self._buffer.clear()

        if body:
            self.sections.append(ParsedSection(text=body, section_title=self._title))

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP:
            self._skipping += 1
            return

        if tag in self._HEADINGS:
            # A heading opens a new section, so whatever came before it belongs
            # to the previous one.
            self._flush()
            self._heading = []
            return

        if tag in self._BLOCKS:
            self._buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skipping = max(self._skipping - 1, 0)
            return

        if tag in self._HEADINGS and self._heading is not None:
            title = " ".join(" ".join(self._heading).split())
            self._heading = None
            self._title = title or self._title

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return

        if self._heading is not None:
            self._heading.append(data)
            return

        self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _parse_html(data: bytes) -> ParsedDocument:
    """Extract prose from an HTML file, split on its headings."""

    reader = _HtmlReader()

    try:
        reader.feed(_decode(data))
        reader.close()
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError(f"HTML could not be read: {exc}") from exc

    return ParsedDocument(sections=reader.sections, page_count=None)


#: A spreadsheet is not a document, and a large one is not a document in a way
#: that matters: a sheet of 50,000 rows embedded whole is a retrieval result
#: that matches everything and means nothing. Rows beyond this are dropped and
#: the section says so, which is honest and keeps the cost bounded.
MAX_SHEET_ROWS = 2000


def _parse_xlsx(data: bytes) -> ParsedDocument:
    """Read each worksheet as a table, one section per sheet.

    `read_only` because the alternative builds the whole workbook in memory,
    and `data_only` because a cell holding `=SUM(B2:B9)` should reach the index
    as the number a reader would see, not as its formula.
    """

    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True
        )
    except Exception as exc:
        raise DocumentParseError(f"Spreadsheet could not be read: {exc}") from exc

    sections: list[ParsedSection] = []

    try:
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            truncated = False

            for position, row in enumerate(sheet.iter_rows(values_only=True)):
                if position >= MAX_SHEET_ROWS:
                    truncated = True
                    break

                cells = [
                    "" if value is None else str(value).strip() for value in row or ()
                ]

                if any(cells):
                    rows.append(cells)

            body = _serialise_rows(rows)

            if truncated:
                body += f"\n[Sheet truncated at {MAX_SHEET_ROWS} rows.]"

            if body.strip():
                sections.append(ParsedSection(text=body, section_title=sheet.title))
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError(f"Spreadsheet could not be read: {exc}") from exc
    finally:
        workbook.close()

    return ParsedDocument(sections=sections, page_count=None)


_PARSERS = {
    "pdf": _parse_pdf,
    "docx": _parse_docx,
    "pptx": _parse_pptx,
    "xlsx": _parse_xlsx,
    "html": _parse_html,
    "md": _parse_markdown,
    "txt": _parse_text,
}


#: Characters no extracted text may carry into the database.
#:
#: U+0000 is the one that matters and the reason this exists: PostgreSQL's
#: `text` cannot hold it at all, and psycopg2 refuses the parameter with
#: `ValueError: A string literal cannot contain NUL (0x00) characters` — an
#: error from outside the domain hierarchy, which therefore surfaced as
#: "Ingestion failed unexpectedly (ValueError)" rather than as anything a
#: reader could act on. SQLite stores NULs without complaint, so the whole test
#: suite passed while three real files in the SunRadia corpus could not be
#: ingested: two UTF-16 HTML exports, and `Summary View.pdf`, an ordinary
#: six-page PDF whose font's ToUnicode CMap maps a handful of glyphs to U+0000.
#:
#: The other C0 and C1 controls go with it. They are never meaningful prose,
#: they render as replacement boxes in a citation, and a corpus that silently
#: differs from what PostgreSQL will accept is the whole class of bug being
#: closed here. Tab, newline and carriage return are kept: they carry the
#: layout that chunking and citation depend on.
_CONTROL_CHARACTERS = {
    codepoint: None
    for codepoint in [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
    if codepoint not in (0x09, 0x0A, 0x0D)
}


def _clean(text: str) -> str:
    """Strip characters the database cannot store from extracted text."""

    return text.translate(_CONTROL_CHARACTERS)


def _cleaned(parsed: ParsedDocument) -> ParsedDocument:
    """Return `parsed` with every section's text and title made storable.

    Applied once, here, rather than in each `_parse_*`: a per-format fix is one
    a new format silently opts out of, and the guarantee wanted is about every
    document that reaches the database, not about the formats someone
    remembered.
    """

    sections = [
        ParsedSection(
            text=cleaned,
            page_number=section.page_number,
            section_title=(
                _clean(section.section_title).strip() or None
                if section.section_title
                else None
            ),
        )
        for section in parsed.sections
        if (cleaned := _clean(section.text).strip())
    ]

    return ParsedDocument(sections=sections, page_count=parsed.page_count)


def parse_document(
    data: bytes, filename: str, content_type: str | None
) -> ParsedDocument:
    """Extract text and provenance from an uploaded file.

    Raises `UnsupportedDocumentTypeError` for unknown formats,
    `DocumentParseError` when a file of a known format cannot be read, and
    `EmptyDocumentError` when it yields no text — the last of which covers
    scanned PDFs with no text layer.

    The emptiness check runs **after** sanitising, deliberately: a file whose
    only content is control characters has no text layer, and saying so is a
    truthful answer where writing a row of stripped-to-nothing sections is not.
    """

    if not data:
        raise EmptyDocumentError("Uploaded file is empty.")

    document_format = resolve_format(filename, content_type)
    parsed = _cleaned(_PARSERS[document_format](data))

    if not parsed.sections:
        raise NoTextLayerError(NO_TEXT_LAYER_REASON)

    return parsed
