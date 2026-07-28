"""Read and delete operations over ingested documents.

Every query filters by `user_id`. Ingestion runs as a single placeholder owner
until authentication exists, but the scoping is enforced from the first query
rather than retrofitted.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import MVP_USER_ID
from app.core.exceptions import DocumentNotFoundError
from app.models.document import Document, DocumentStatus


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


def delete_document(
    db: Session, document_id: uuid.UUID, *, user_id: str = MVP_USER_ID
) -> None:
    """Delete a document and, by cascade, all of its chunks."""

    document = get_document(db, document_id, user_id=user_id)
    db.delete(document)
    db.commit()
