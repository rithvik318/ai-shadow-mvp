"""Splitting parsed text into overlapping chunks that keep their provenance.

Each parsed section is split independently so that a chunk never spans two
pages or two headings — which is what makes a citation resolvable back to a
specific page later.
"""

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.settings import settings
from app.services.features.documents.parser_service import ParsedDocument


@dataclass(frozen=True)
class TextChunk:
    """One embeddable slice of a document."""

    index: int
    content: str
    char_count: int
    page_number: int | None = None
    section_title: str | None = None


def chunk_document(
    parsed: ParsedDocument,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[TextChunk]:
    """Split a parsed document into ordered, overlapping chunks.

    `chunk_index` is global across the document, so chunks sort back into
    reading order regardless of which section they came from.
    """

    size = chunk_size if chunk_size is not None else settings.CHUNK_SIZE
    overlap = chunk_overlap if chunk_overlap is not None else settings.CHUNK_OVERLAP

    if size <= 0:
        raise ValueError("chunk_size must be greater than zero.")
    if overlap < 0:
        raise ValueError("chunk_overlap cannot be negative.")
    if overlap >= size:
        raise ValueError("chunk_overlap must be smaller than chunk_size.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        length_function=len,
    )

    chunks: list[TextChunk] = []

    for section in parsed.sections:
        for piece in splitter.split_text(section.text):
            content = piece.strip()
            if not content:
                continue

            chunks.append(
                TextChunk(
                    index=len(chunks),
                    content=content,
                    char_count=len(content),
                    page_number=section.page_number,
                    section_title=section.section_title,
                )
            )

    return chunks
