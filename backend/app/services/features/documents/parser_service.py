"""Extraction of plain text and provenance metadata from uploaded files.

Pure functions over bytes: no database access, no configuration, no network.
That keeps the format-specific logic — which is where the awkward edge cases
live — independently testable.
"""

import io
import re
from dataclasses import dataclass
from pathlib import Path

import docx
import pypdf

from app.core.constants import SUPPORTED_CONTENT_TYPES, SUPPORTED_EXTENSIONS
from app.core.exceptions import (
    DocumentParseError,
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
)

_MARKDOWN_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*$")
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


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

    raise UnsupportedDocumentTypeError(
        f"Unsupported document type: {content_type or extension or 'unknown'}. "
        f"Supported formats are PDF, DOCX, TXT and Markdown."
    )


def _decode(data: bytes) -> str:
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


def _parse_docx(data: bytes) -> ParsedDocument:
    """Extract DOCX paragraphs, grouped under their nearest heading.

    DOCX has no reliable page boundaries without rendering the document, so
    `page_number` stays None and headings carry the provenance instead.
    """

    try:
        document = docx.Document(io.BytesIO(data))
        paragraphs = list(document.paragraphs)
    except Exception as exc:
        raise DocumentParseError(f"DOCX could not be read: {exc}") from exc

    sections: list[ParsedSection] = []
    current_title: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(ParsedSection(text=body, section_title=current_title))
        buffer.clear()

    for paragraph in paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue

        style_name = (paragraph.style.name or "") if paragraph.style else ""
        if style_name.lower().startswith("heading") or style_name == "Title":
            flush()
            current_title = text
            continue

        buffer.append(text)

    flush()
    return ParsedDocument(sections=sections, page_count=None)


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


_PARSERS = {
    "pdf": _parse_pdf,
    "docx": _parse_docx,
    "md": _parse_markdown,
    "txt": _parse_text,
}


def parse_document(
    data: bytes, filename: str, content_type: str | None
) -> ParsedDocument:
    """Extract text and provenance from an uploaded file.

    Raises `UnsupportedDocumentTypeError` for unknown formats,
    `DocumentParseError` when a file of a known format cannot be read, and
    `EmptyDocumentError` when it yields no text — the last of which covers
    scanned PDFs with no text layer.
    """

    if not data:
        raise EmptyDocumentError("Uploaded file is empty.")

    document_format = resolve_format(filename, content_type)
    parsed = _PARSERS[document_format](data)

    if not parsed.sections:
        raise EmptyDocumentError(
            "No text could be extracted from the document. Scanned documents "
            "without a text layer are not supported."
        )

    return parsed
