"""Ingestion through to retrieval, using the real search path.

The assertions elsewhere in this directory are about rows. These are about
whether the knowledge base actually answers with the right text — which is the
only reason the rows matter. They run the same `retrieval_service.search` that
`/search` and `/chat` call, rather than inspecting `document_chunks` and
trusting that retrieval would agree.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models.document import DocumentStatus
from app.services.features.documents.document_service import delete_document
from app.services.features.documents.ingestion_service import (
    ingest_document,
    ingest_file,
)
from app.services.features.retrieval.retrieval_service import search
from tests.fixtures.factories import build_markdown, build_pdf, build_text
from tests.support.embeddings import deterministic_vector

MARKDOWN_TYPE = "text/markdown"
PDF_TYPE = "application/pdf"
TEXT_TYPE = "text/plain"

ORIGINAL = "The original statement about municipal water treatment."
REVISED = "A revised statement about railway signalling instead."


def _find(db: Session, text: str, embed_query_as: Callable[[list[float]], list[str]]):
    """Return everything retrieval can reach, with the query pinned to `text`.

    The fake provider hashes text to a vector, so pinning the query vector to
    `deterministic_vector(text)` makes the chunk holding exactly that text the
    nearest neighbour. The similarity floor is disabled: these tests are about
    what is reachable at all, not about tuning it.
    """

    embed_query_as(deterministic_vector(text))

    return search(db, "any question", similarity_threshold=None)


def _containing(results, needle: str):
    """Pick a result by what it says rather than by where it ranked.

    Extraction normalises whitespace differently per format, so a chunk's text
    is not always byte-identical to what went in — which makes its exact rank
    an unsafe thing to assert when the point of the test is its metadata.
    """

    matches = [result for result in results if needle in result.content]
    assert matches, f"no retrieved passage contained {needle!r}"

    return matches[0]


# --- a document reaches retrieval ----------------------------------------


def test_an_ingested_document_is_searchable(
    db_session: Session, embed_query_as
) -> None:
    ingest_document(
        db_session,
        data=build_text(ORIGINAL),
        filename="statement.txt",
        content_type=TEXT_TYPE,
    )

    results = _find(db_session, ORIGINAL, embed_query_as)

    assert [result.content for result in results][:1] == [ORIGINAL]


def test_retrieval_carries_the_source_metadata_rag_cites(
    db_session: Session, embed_query_as
) -> None:
    """Chat renders `[SOURCE n]` from these fields. If ingestion stops
    populating them, citations degrade quietly rather than failing."""

    document = ingest_document(
        db_session,
        data=build_markdown([("Water Treatment", ORIGINAL)]),
        filename="statement.md",
        content_type=MARKDOWN_TYPE,
    )

    top = _containing(_find(db_session, ORIGINAL, embed_query_as), "municipal water")

    assert top.document_id == document.id
    assert top.filename == "statement.md"
    assert top.section_title == "Water Treatment"
    assert top.chunk_index == 0


def test_page_numbers_survive_ingestion(db_session: Session, embed_query_as) -> None:
    """A citation that cannot name a page is a citation to a whole document."""

    ingest_document(
        db_session,
        data=build_pdf(["Page one is about something else.", ORIGINAL]),
        filename="statement.pdf",
        content_type=PDF_TYPE,
    )

    top = _containing(_find(db_session, ORIGINAL, embed_query_as), "municipal water")

    assert top.page_number == 2


# --- replacement ---------------------------------------------------------


def test_replaced_content_is_no_longer_retrievable(
    db_session: Session, embed_query_as
) -> None:
    """The assertion this whole component exists for. A document that was
    edited must stop answering with what it used to say."""

    ingest_document(
        db_session,
        data=build_text(ORIGINAL),
        filename="statement.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )
    ingest_document(
        db_session,
        data=build_text(REVISED),
        filename="statement.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    results = _find(db_session, ORIGINAL, embed_query_as)

    assert all(ORIGINAL not in result.content for result in results)


def test_the_replacement_is_retrievable(db_session: Session, embed_query_as) -> None:
    """Removing the old text is only half of it; the new text has to arrive."""

    ingest_document(
        db_session,
        data=build_text(ORIGINAL),
        filename="statement.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )
    ingest_document(
        db_session,
        data=build_text(REVISED),
        filename="statement.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    results = _find(db_session, REVISED, embed_query_as)

    assert [result.content for result in results][:1] == [REVISED]


# --- removal -------------------------------------------------------------


def test_a_deleted_document_is_not_retrievable(
    db_session: Session, embed_query_as
) -> None:
    document = ingest_document(
        db_session,
        data=build_text(ORIGINAL),
        filename="statement.txt",
        content_type=TEXT_TYPE,
    )

    delete_document(db_session, document.id)

    assert _find(db_session, ORIGINAL, embed_query_as) == []


def test_a_failed_document_is_not_retrievable(
    db_session: Session, embed_query_as
) -> None:
    """A document whose ingestion failed keeps whatever chunks it had, and the
    status is what has to keep them out of an answer."""

    document = ingest_document(
        db_session,
        data=build_text(ORIGINAL),
        filename="statement.txt",
        content_type=TEXT_TYPE,
    )

    document.status = DocumentStatus.FAILED
    db_session.commit()

    assert _find(db_session, ORIGINAL, embed_query_as) == []


def test_an_unsupported_document_is_not_retrievable(
    db_session: Session, embed_query_as
) -> None:
    """`unsupported` is a new status. Retrieval filters on `indexed`, so it is
    excluded — this pins that, because a status that leaked into search would
    serve the text of a file the system says it cannot read."""

    ingest_document(
        db_session,
        data=build_text(ORIGINAL),
        filename="statement.txt",
        content_type=TEXT_TYPE,
        source_uri="onedrive:item-1",
    )

    ingest_file(
        db_session,
        data=b"legacy binary",
        filename="statement.doc",
        content_type="application/msword",
        source_uri="onedrive:item-1",
    )

    assert _find(db_session, ORIGINAL, embed_query_as) == []


# --- the shared corpus stays shared --------------------------------------


def test_the_knowledge_base_is_shared_across_digital_twins(
    db_session: Session, embed_query_as
) -> None:
    """Ingestion defaults to the shared corpus owner. Two people's chats read
    the same knowledge base, and only their Digital Twins differ — making
    documents user-private here would silently split the company corpus."""

    document = ingest_document(
        db_session,
        data=build_text(ORIGINAL),
        filename="statement.txt",
        content_type=TEXT_TYPE,
    )

    assert document.user_id == "mvp-user"
    found = _containing(_find(db_session, ORIGINAL, embed_query_as), "municipal water")

    assert found.document_id == document.id
