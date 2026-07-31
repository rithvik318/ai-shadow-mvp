class AIShadowError(Exception):
    """Base class for every domain error raised by this application."""


# --- Prompt system -------------------------------------------------------


class PromptTemplateError(AIShadowError):
    """Raised when a prompt template cannot be rendered."""


class PromptNotFoundError(AIShadowError):
    """Raised when a requested prompt template does not exist."""


class DuplicatePromptError(AIShadowError):
    """Raised when a prompt template is registered more than once."""


# --- LLM / analysis ------------------------------------------------------


class LLMServiceError(AIShadowError):
    """Raised when the LLM provider call fails."""


class AnalysisValidationError(LLMServiceError):
    """Raised when an LLM response cannot be parsed or validated against the
    expected schema."""


class EmbeddingError(LLMServiceError):
    """Raised when embeddings cannot be generated for one or more texts."""


class EmbeddingDimensionError(EmbeddingError):
    """Raised when the provider returns vectors of an unexpected width.

    Storing these would corrupt the index silently: pgvector rejects the write
    only if the column width differs, and a model swapped for one of the same
    width but different semantics would not be caught at all.
    """


# --- Documents -----------------------------------------------------------


class DocumentError(AIShadowError):
    """Base class for document ingestion failures."""


class UnsupportedDocumentTypeError(DocumentError):
    """Raised when an upload's type is not one of the supported formats."""


class DocumentTooLargeError(DocumentError):
    """Raised when an upload exceeds the configured maximum size."""


class EmptyDocumentError(DocumentError):
    """Raised when an upload contains no bytes, or no extractable text."""


class DocumentParseError(DocumentError):
    """Raised when a document is the right type but cannot be read."""


class DocumentNotFoundError(DocumentError):
    """Raised when a requested document does not exist for this user."""


# --- Retrieval -----------------------------------------------------------


class RetrievalError(AIShadowError):
    """Base class for search failures caused by the caller's request.

    Distinct from `LLMServiceError`: a bad `top_k` is the client's mistake,
    while a provider outage is not, and the two must not share a status code.
    """


class EmptyQueryError(RetrievalError):
    """Raised when a search query is blank."""
