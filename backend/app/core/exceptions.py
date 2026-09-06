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


class CalendarError(AIShadowError):
    """Base class for failures about meetings and attendance."""


class EventNotFoundError(CalendarError):
    """Raised when no event with that id belongs to the calling user.

    Not-found rather than forbidden: confirming that a row exists but belongs
    to somebody else is itself a disclosure.
    """


class EventValidationError(CalendarError):
    """Raised when an event's own details do not make sense."""


class SourceUnresolvableError(SyncError):
    """Raised when a configured folder cannot be turned into a drive item.

    Carries a `remedy` because the useful information is rarely the failure
    itself. "The folder is in a consumer OneDrive" and "the application has no
    consent for this site" both read as a refusal and have entirely different
    answers — one is a decision for whoever owns the content, the other for a
    tenant administrator — and a message that does not distinguish them sends
    somebody granting permissions that cannot possibly help.
    """

    def __init__(self, message: str, *, remedy: str | None = None) -> None:
        super().__init__(message)
        self.remedy = remedy

    def __str__(self) -> str:
        base = super().__str__()

        return f"{base} {self.remedy}" if self.remedy else base


class SyncAlreadyRunningError(SyncError):
    """Raised when a source is asked to synchronise while it already is.

    The claim lives in the database rather than in process memory, because the
    scheduler and a manual API call are two different callers and may not even
    be the same process.
    """


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


class NotAuthorisedError(IdentityError):
    """The caller is known, but is not allowed to do this.

    Distinct from the identity errors around it: those mean "say who you are",
    this means "you said, and it is not enough". Collapsing them would send a
    normal user to a login screen they do not need.
    """


class DuplicateUserError(IdentityError):
    """Raised when a user is created with an email that already exists."""


# --- Email ---------------------------------------------------------------


class EmailError(AIShadowError):
    """Base class for every failure in the email module."""


class EmailValidationError(EmailError):
    """Raised when a draft or request is not well enough formed to act on.

    The caller's to fix, and deliberately not a provider error: "no recipients"
    and "Outlook is down" must not share a status code, or a client cannot tell
    whether retrying is worth anything.
    """


class EmailTemplateNotFoundError(EmailError):
    """Raised when a template does not exist for this user.

    The same error whether the template is absent or belongs to somebody else,
    for the reason `MemoryNotFoundError` gives: confirming that it exists is a
    fact about another person's workspace.
    """


class DuplicateEmailTemplateError(EmailError):
    """Raised when a user already has a template with that name."""


class EmailDraftNotFoundError(EmailError):
    """Raised when a draft does not exist for this user."""


class EmailDraftNotApprovedError(EmailError):
    """Raised when sending is attempted on a draft nobody has approved.

    The whole human-in-the-loop guarantee is this exception. Approval is a
    separate, explicit act recorded on the row, and editing a draft revokes it
    — so an approved draft is always the text that was actually read.
    """


class EmailDraftAlreadySentError(EmailError):
    """Raised when a draft that has already left is asked to leave again."""


class EmailAttachmentError(EmailError):
    """Base class for attachment failures."""


class EmailAttachmentTooLargeError(EmailAttachmentError):
    """Raised when one attachment exceeds the configured size limit."""


class TooManyAttachmentsError(EmailAttachmentError):
    """Raised when a draft would hold more attachments than are allowed."""


class EmptyAttachmentError(EmailAttachmentError):
    """Raised when an attachment carries no bytes.

    Rejected rather than stored: a zero-byte file is almost always a failed
    read on the client, and discovering that at send time is too late.
    """


class EmailProviderError(EmailError):
    """Base class for failures talking to a mailbox provider."""


class EmailProviderNotConfiguredError(EmailProviderError):
    """Raised when a mailbox operation is asked for and none is connected.

    Not a failure. Drafting, templates and generation all work without a
    mailbox; this is the honest answer to "list my inbox" on a deployment that
    has not been given one, and is what stops the UI inventing messages.
    """


class EmailProviderAuthError(EmailProviderError):
    """Raised when the provider rejects the application's credentials."""


class EmailSendError(EmailProviderError):
    """Raised when the provider accepted the request and did not send.

    Distinct from every error above because of what it must *not* do: a draft
    that raises this is recorded as `failed`, never as `sent`.
    """


class TaskError(AIShadowError):
    """Anything wrong with a task or a report built from tasks."""


class TaskNotFoundError(TaskError):
    """No task with that id belongs to this user.

    Deliberately not distinguished from "exists but belongs to somebody else".
    A 403 there would confirm that another person's task exists, which is the
    leak the user-scoped lookup exists to prevent.
    """


class TaskValidationError(TaskError):
    """A task field was not usable — an empty title, most often."""


class ReportError(AIShadowError):
    """Anything wrong with generating or reading back a stored report."""


class ReportPeriodError(ReportError):
    """The requested period is not one this system can name.

    A 400 rather than a fall back to the current period. Answering a request
    for a badly spelled week with *this* week would show somebody a report they
    did not ask for and give them no way to tell — the one failure mode a
    report must not have.
    """


class TaskTransitionError(TaskError):
    """The task exists and is the caller's, and this move is not allowed from
    where it is.

    A 409 rather than a 422, for the same reason an unapproved draft is: the
    request is well formed and the caller is entitled to make it — the server
    is simply not in a state where it can be honoured. The remedy is an action
    on the task, not a correction to the request.

    Completion is the only terminal state. Starting a finished task would clear
    its completion stamp, and a lifecycle in which "done" can quietly become
    "in progress" makes every completed count a claim about the present rather
    than a record of what happened.
    """
