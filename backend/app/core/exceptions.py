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
