"""Context assembly is a pure function, so it is tested as one.

No database and no provider: these assertions are about how a passage is
presented to the model and how many of them fit, which is exactly what breaks
when answers start citing badly.
"""

import uuid

from app.services.features.chat.context_service import build_context
from app.services.features.retrieval.retrieval_service import RetrievedChunk

WIDE = 100_000


def _chunk(
    content: str,
    *,
    similarity: float = 0.9,
    filename: str = "handbook.pdf",
    page_number: int | None = None,
    section_title: str | None = None,
    chunk_index: int = 0,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename=filename,
        chunk_index=chunk_index,
        content=content,
        similarity=similarity,
        page_number=page_number,
        section_title=section_title,
    )


def test_each_passage_becomes_a_numbered_delimited_source() -> None:
    """The model needs an identifier per passage to be able to cite one."""

    context = build_context(
        [_chunk("First passage."), _chunk("Second passage.")], max_chars=WIDE
    )

    assert context.text == (
        "[SOURCE 1]\nDocument: handbook.pdf\nContent: First passage.\n"
        "\n"
        "[SOURCE 2]\nDocument: handbook.pdf\nContent: Second passage."
    )


def test_retrieval_order_is_preserved() -> None:
    """Ranking is the database's, computed against the index; it is not redone."""

    chunks = [
        _chunk("Most relevant.", similarity=0.91),
        _chunk("Middling.", similarity=0.62),
        _chunk("Least relevant.", similarity=0.30),
    ]

    text = build_context(chunks, max_chars=WIDE).text

    assert text.index("Most relevant.") < text.index("Middling.")
    assert text.index("Middling.") < text.index("Least relevant.")


def test_a_docx_passage_is_labelled_by_section() -> None:
    """DOCX has no page without rendering, so the heading carries provenance."""

    context = build_context(
        [_chunk("Body.", filename="policy.docx", section_title="Data Governance")],
        max_chars=WIDE,
    )

    assert "Section: Data Governance" in context.text
    assert "Page:" not in context.text


def test_a_pptx_passage_is_labelled_by_slide_ordinal() -> None:
    """Slide number rides in `page_number` until the locator is named."""

    context = build_context(
        [_chunk("Slide body.", filename="deck.pptx", page_number=7)], max_chars=WIDE
    )

    assert "Page: 7" in context.text


def test_both_locators_appear_when_the_format_has_both() -> None:
    context = build_context(
        [_chunk("Body.", page_number=3, section_title="Scope")], max_chars=WIDE
    )

    assert "Section: Scope" in context.text
    assert "Page: 3" in context.text


def test_the_budget_drops_the_least_relevant_passages_first() -> None:
    """A bounded context must cost the material that matters least."""

    chunks = [
        _chunk("A" * 200, similarity=0.9),
        _chunk("B" * 200, similarity=0.8),
        _chunk("C" * 200, similarity=0.7),
    ]

    context = build_context(chunks, max_chars=500)

    assert [c.content[0] for c in context.chunks] == ["A", "B"]
    assert "C" * 200 not in context.text
    assert len(context.text) <= 500


def test_assembled_chunks_are_what_the_model_actually_saw() -> None:
    """Sources are built from these, so a dropped passage must not be listed."""

    chunks = [_chunk("A" * 300), _chunk("B" * 300)]

    context = build_context(chunks, max_chars=350)

    assert len(context.chunks) == 1
    assert context.chunks[0] is chunks[0]


def test_a_single_oversized_passage_is_still_included() -> None:
    """Dropping it would leave the model nothing to answer from."""

    chunks = [_chunk("A" * 5000)]

    context = build_context(chunks, max_chars=100)

    assert context.chunks == chunks
    assert "A" * 5000 in context.text


def test_no_chunks_produce_an_empty_context() -> None:
    context = build_context([], max_chars=WIDE)

    assert context.text == ""
    assert context.chunks == []
