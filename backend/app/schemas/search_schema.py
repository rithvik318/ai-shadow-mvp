import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchRequest(BaseModel):
    """A query to run against the caller's indexed documents.

    Identical in shape to `ChatRequest` today, and deliberately not derived
    from it: the two are the same by coincidence rather than by obligation.
    Search is a debugging surface and chat is a product surface, so binding
    them together would let a change made for one silently alter the other's
    contract.
    """

    question: str = Field(
        min_length=1,
        max_length=4000,
        description="The text to search your documents for.",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Passages to return. Defaults to RETRIEVAL_TOP_K.",
    )

    @field_validator("question")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """`min_length` alone would accept a string of spaces."""

        if not value.strip():
            raise ValueError("question cannot be blank")

        return value


class SearchResultResponse(BaseModel):
    """One retrieved passage, with its text and where it came from.

    Carries `content`, which `ChatSourceResponse` does not. The point of this
    endpoint is to see what the model would have been handed, and a similarity
    score without the passage it scores cannot show why a retrieval went
    wrong.

    `page` carries whichever ordinal the format has: a page for PDF, a slide
    for PPTX. DOCX has neither without rendering the file, so it is null there
    and `section` names the heading instead. TXT and Markdown may have neither.
    """

    model_config = ConfigDict(from_attributes=True)

    document: str = Field(description="Filename of the source document.")
    section: str | None = Field(
        default=None, description="Heading the passage sits under, where known."
    )
    page: int | None = Field(
        default=None, description="Page for PDF, slide number for PPTX."
    )
    similarity: float = Field(description="Cosine similarity to the query.")
    content: str = Field(description="The passage itself, as it is stored.")

    # Retained alongside the fields above, as in `ChatSourceResponse`, so a
    # result can be traced back to the stored row without a lookup by filename.
    document_id: uuid.UUID
    chunk_id: uuid.UUID


class SearchResponse(BaseModel):
    """The passages retrieval would put in front of the model, and nothing
    else.

    `retrieved_count == 0` means nothing in the knowledge base cleared the
    similarity floor — or that there is nothing in it at all. Either way it is
    an answer to the query, not a failure.
    """

    query: str = Field(description="The query as it was searched for.")
    results: list[SearchResultResponse]
    retrieved_count: int
