import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    """A question to answer from the caller's indexed documents."""

    question: str = Field(
        min_length=1,
        max_length=4000,
        description="The question to answer from your documents.",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Passages to retrieve. Defaults to RETRIEVAL_TOP_K.",
    )

    @field_validator("question")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """`min_length` alone would accept a string of spaces."""

        if not value.strip():
            raise ValueError("question cannot be blank")

        return value


class ChatSourceResponse(BaseModel):
    """A passage the answer was allowed to draw on.

    `page` carries whichever ordinal the format has: a page for PDF, a slide
    for PPTX. DOCX has neither without rendering the file, so it is null there
    and `section` names the heading instead. Naming the two locators apart is
    the `chunk_metadata` work in docs/ROADMAP.md; until then one field holds
    both and the document's type says which it means.
    """

    model_config = ConfigDict(from_attributes=True)

    document: str = Field(description="Filename of the source document.")
    section: str | None = Field(
        default=None, description="Heading the passage sits under, where known."
    )
    page: int | None = Field(
        default=None, description="Page for PDF, slide number for PPTX."
    )
    similarity: float = Field(description="Cosine similarity to the question.")

    # Retained alongside the four fields above so a caller can link a citation
    # back to the stored row without a second lookup by filename.
    document_id: uuid.UUID
    chunk_id: uuid.UUID


class ChatResponse(BaseModel):
    """A grounded answer, with the passages that were put in front of the
    model.

    `retrieved_chunks == 0` means nothing in the knowledge base was relevant —
    the answer says so, and no model call was made.
    """

    answer: str
    sources: list[ChatSourceResponse]
    retrieved_chunks: int
