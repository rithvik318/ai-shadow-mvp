import pytest

from app.services.features.documents.chunker_service import chunk_document
from app.services.features.documents.parser_service import (
    ParsedDocument,
    ParsedSection,
)


def _document(
    *sections: ParsedSection, page_count: int | None = None
) -> ParsedDocument:
    return ParsedDocument(sections=list(sections), page_count=page_count)


def test_short_section_becomes_a_single_chunk() -> None:
    """Text below the chunk size is not split."""

    parsed = _document(ParsedSection(text="Short body of text."))

    chunks = chunk_document(parsed, chunk_size=1000, chunk_overlap=100)

    assert len(chunks) == 1
    assert chunks[0].content == "Short body of text."
    assert chunks[0].char_count == len("Short body of text.")


def test_long_section_is_split_into_multiple_chunks() -> None:
    """Text above the chunk size is split."""

    parsed = _document(ParsedSection(text=" ".join(["word"] * 400)))

    chunks = chunk_document(parsed, chunk_size=200, chunk_overlap=20)

    assert len(chunks) > 1
    assert all(chunk.char_count <= 200 for chunk in chunks)


def test_chunk_indices_are_sequential_and_global() -> None:
    """Indices run 0..n-1 across the whole document, not per section."""

    parsed = _document(
        ParsedSection(text=" ".join(["alpha"] * 100), page_number=1),
        ParsedSection(text=" ".join(["beta"] * 100), page_number=2),
    )

    chunks = chunk_document(parsed, chunk_size=120, chunk_overlap=20)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_page_number_is_preserved_on_every_chunk() -> None:
    """A chunk knows which page it came from, which is what makes a citation
    resolvable later."""

    parsed = _document(
        ParsedSection(text=" ".join(["alpha"] * 80), page_number=1),
        ParsedSection(text=" ".join(["beta"] * 80), page_number=2),
    )

    chunks = chunk_document(parsed, chunk_size=100, chunk_overlap=10)

    assert {chunk.page_number for chunk in chunks} == {1, 2}
    for chunk in chunks:
        expected = 1 if "alpha" in chunk.content else 2
        assert chunk.page_number == expected


def test_section_title_is_preserved_on_every_chunk() -> None:
    """Heading provenance survives splitting."""

    parsed = _document(
        ParsedSection(text=" ".join(["body"] * 80), section_title="Methods")
    )

    chunks = chunk_document(parsed, chunk_size=100, chunk_overlap=10)

    assert all(chunk.section_title == "Methods" for chunk in chunks)


def test_chunks_never_span_two_sections() -> None:
    """Sections are split independently, so no chunk mixes two pages."""

    parsed = _document(
        ParsedSection(text="alpha " * 30, page_number=1),
        ParsedSection(text="beta " * 30, page_number=2),
    )

    chunks = chunk_document(parsed, chunk_size=2000, chunk_overlap=0)

    for chunk in chunks:
        assert not ("alpha" in chunk.content and "beta" in chunk.content)


def test_overlap_repeats_content_between_neighbouring_chunks() -> None:
    """Consecutive chunks share text, so meaning is not cut at a boundary."""

    parsed = _document(ParsedSection(text=" ".join(f"w{i}" for i in range(300))))

    with_overlap = chunk_document(parsed, chunk_size=200, chunk_overlap=80)
    without_overlap = chunk_document(parsed, chunk_size=200, chunk_overlap=0)

    assert len(with_overlap) > len(without_overlap)


def test_settings_supply_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chunk size and overlap come from configuration when not passed."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "CHUNK_SIZE", 100)
    monkeypatch.setattr(settings_module.settings, "CHUNK_OVERLAP", 10)

    parsed = _document(ParsedSection(text=" ".join(["word"] * 200)))

    chunks = chunk_document(parsed)

    assert all(chunk.char_count <= 100 for chunk in chunks)


def test_empty_document_produces_no_chunks() -> None:
    """A document with no sections chunks to nothing rather than raising."""

    assert chunk_document(_document()) == []


def test_whitespace_only_pieces_are_dropped() -> None:
    """Splitting never emits a blank chunk."""

    parsed = _document(ParsedSection(text="alpha\n\n\n\n\n\nbeta"))

    chunks = chunk_document(parsed, chunk_size=10, chunk_overlap=0)

    assert all(chunk.content.strip() for chunk in chunks)


@pytest.mark.parametrize(
    "chunk_size, chunk_overlap",
    [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 150)],
)
def test_invalid_chunk_configuration_is_rejected(
    chunk_size: int, chunk_overlap: int
) -> None:
    """Non-positive sizes and overlaps that meet or exceed the size raise."""

    parsed = _document(ParsedSection(text="body"))

    with pytest.raises(ValueError):
        chunk_document(parsed, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
