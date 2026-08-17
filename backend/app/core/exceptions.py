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


class BatchTooLargeError(DocumentError):
    """Raised when one upload request carries more files than are allowed.

    A limit on the number of files, not their combined size, because the cost
    that matters here is time: ingestion is synchronous, so the request is held
    open for as long as the whole batch takes.
    """


# --- Retrieval -----------------------------------------------------------


class RetrievalError(AIShadowError):
    """Base class for search failures caused by the caller's request.

    Distinct from `LLMServiceError`: a bad `top_k` is the client's mistake,
    while a provider outage is not, and the two must not share a status code.
    """


class EmptyQueryError(RetrievalError):
    """Raised when a search query is blank."""


# --- Digital Twin --------------------------------------------------------


class DigitalTwinError(AIShadowError):
    """Base class for profile and memory failures."""


class ProfileNotFoundError(DigitalTwinError):
    """Raised when no Digital Twin profile has been set up."""


class ProfileIncompleteError(DigitalTwinError):
    """Raised when the first write of a profile omits a required field.

    Separate from `ProfileNotFoundError` because the caller's remedy differs:
    one means "set one up", the other means "you nearly did".
    """


class MemoryNotFoundError(DigitalTwinError):
    """Raised when a requested memory does not exist for this user.

    Deliberately the same error whether the memory is absent or belongs to
    somebody else: telling a caller that a memory exists but is not theirs
    tells them something about another person's Digital Twin.
    """


# --- Synchronisation -----------------------------------------------------


class SyncError(AIShadowError):
    """Base class for failures to synchronise an external source."""


class SyncNotConfiguredError(SyncError):
    """Raised when a sync is requested but no credentials or sources exist.

    Distinct from a failure: nothing is wrong, the deployment simply has not
    been given anything to synchronise.
    """


class SyncSourceNotFoundError(SyncError):
    """Raised when a named source is not in the configured set."""


class GraphError(SyncError):
    """Base class for failures talking to Microsoft Graph."""


class GraphAuthError(GraphError):
    """Raised when Graph rejects the application's credentials.

    Separate from a transport failure because the remedy is different: a
    token that will not mint again is a configuration problem, and retrying
    it on a schedule only produces the same answer more often.
    """


class GraphRequestError(GraphError):
    """Raised when a Graph call fails for any other reason."""


class DeltaTokenExpiredError(GraphError):
    """Raised when Graph refuses a delta token and demands a full resync.

    Not really an error — it is Graph telling the caller that too much has
    changed, or too much time has passed, for an incremental answer to be
    correct. The caller is expected to start again from a full enumeration.
    """


# --- Identity ------------------------------------------------------------


class IdentityError(AIShadowError):
    """Base class for failures to establish who a request is for."""


class MissingIdentityError(IdentityError):
    """Raised when a request that needs a Digital Twin carries no identity."""


class MalformedIdentityError(IdentityError):
    """Raised when the identity on a request is not a well-formed user id."""


class UserNotFoundError(IdentityError):
    """Raised when the identity on a request names nobody."""


class DuplicateUserError(IdentityError):
    """Raised when a user is created with an email that already exists."""
