"""Read and delete operations over ingested documents.

Every query filters by `user_id`. Ingestion runs as a single placeholder owner
until authentication exists, but the scoping is enforced from the first query
rather than retrofitted.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.core.exceptions import DocumentNotFoundError
from app.models.document import Document, DocumentChunk, DocumentStatus


def list_documents(
    db: Session,
    *,
    user_id: str = MVP_USER_ID,
    status: DocumentStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Document], int]:
    """Return one page of the user's documents, newest first, plus the total."""

    filters = [Document.user_id == user_id]
    if status is not None:
        filters.append(Document.status == status)

    total = db.execute(
        select(func.count()).select_from(Document).where(*filters)
    ).scalar_one()

    documents = (
        db.execute(
            select(Document)
            .where(*filters)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return list(documents), total


def get_document(
    db: Session, document_id: uuid.UUID, *, user_id: str = MVP_USER_ID
) -> Document:
    """Return one document, or raise `DocumentNotFoundError`."""

    document = db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    ).scalar_one_or_none()

    if document is None:
        raise DocumentNotFoundError(f"Document not found: {document_id}")

    return document


def find_document_by_source(
    db: Session, source_uri: str, *, user_id: str = MVP_USER_ID
) -> Document | None:
    """Return the document ingested from `source_uri`, if there is one.

    The lookup a synchronisation needs in order to act on a file it can no
    longer see: the external system reports an id, and this turns that id back
    into a row without going anywhere near the filename.
    """

    return db.execute(
        select(Document).where(
            Document.user_id == user_id, Document.source_uri == source_uri
        )
    ).scalar_one_or_none()


def delete_document(
    db: Session, document_id: uuid.UUID, *, user_id: str = MVP_USER_ID
) -> None:
    """Delete a document and, by cascade, all of its chunks."""

    document = get_document(db, document_id, user_id=user_id)
    db.delete(document)
    db.commit()


def delete_document_by_source(
    db: Session, source_uri: str, *, user_id: str = MVP_USER_ID
) -> bool:
    """Delete whatever was ingested from `source_uri`. Absence is not an error.

    Returns whether anything was removed. A sync reporting a deletion for a
    file this system never indexed — an unsupported format, or one added and
    removed between runs — is ordinary, not a fault, so this does not raise
    the way `delete_document` does.
    """

    document = find_document_by_source(db, source_uri, user_id=user_id)

    if document is None:
        return False

    db.delete(document)
    db.commit()

    return True


@dataclass(frozen=True)
class CorpusStats:
    """How much is in the knowledge base, counted in the database.

    Counted rather than derived from a page of results. The knowledge base is
    a corpus, not a list: any answer built from the fifty documents a listing
    happened to return is wrong as soon as there are fifty-one, and a status
    screen that quietly under-reports is worse than one that says nothing.
    """

    documents: int
    chunks: int
    by_status: dict[str, int]
    embedded_chunks: int


def corpus_stats(db: Session, *, user_id: str = MVP_USER_ID) -> CorpusStats:
    """Aggregate counts over the whole corpus, in three queries."""

    by_status = {status.value: 0 for status in DocumentStatus}

    rows = db.execute(
        select(Document.status, func.count())
        .where(Document.user_id == user_id)
        .group_by(Document.status)
    ).all()

    for status, count in rows:
        # SQLAlchemy hands back the enum member; the key is its value so the
        # mapping serialises without a second conversion.
        by_status[status.value if hasattr(status, "value") else str(status)] = count

    chunks = db.execute(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.user_id == user_id)
    ).scalar_one()

    # An indexed document with unembedded chunks is retrievable by nothing, so
    # the gap between these two numbers is worth being able to see.
    embedded = db.execute(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.user_id == user_id, DocumentChunk.embedding.is_not(None))
    ).scalar_one()

    return CorpusStats(
        documents=sum(by_status.values()),
        chunks=chunks,
        by_status=by_status,
        embedded_chunks=embedded,
    )
