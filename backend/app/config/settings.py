from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables.

    Every value has a usable development default so that the application and
    its test suite can be imported without a populated `.env` — a deliberate
    change from the reference repository, where missing variables made the
    package unimportable and therefore untestable in CI.
    """

    APP_NAME: str = "AI Shadow MVP"
    DEBUG: bool = False

    DATABASE_URL: str = (
        "postgresql+psycopg2://aishadow:aishadow@localhost:5432/aishadow"
    )

    MAX_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024
    # Ingestion is synchronous, so a batch holds a request open for the sum
    # of its files. This bounds that, and is the number a sync source should
    # page by.
    MAX_BATCH_UPLOAD_FILES: int = 25
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 150

    EMBEDDING_DIMENSIONS: int = 1536
    EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
    # Unset means "use LLM_PROVIDER". Set it only to point embeddings at a
    # different provider from completions.
    EMBEDDING_PROVIDER: str | None = None
    EMBEDDING_BATCH_SIZE: int = 64

    # How many chunks a search returns, and the cosine-similarity floor a chunk
    # must clear. The floor defaults to 0.0 — orthogonal or better — because a
    # useful value can only be chosen against real documents; see docs/ROADMAP.md.
    # Set to None (an empty value in .env) to disable filtering entirely.
    # How much retrieved text is rendered into the prompt. Bounds the
    # request when several long passages are retrieved at once; the least
    # relevant are dropped first, and a dropped passage is not reported as
    # a source because the model never saw it.
    CHAT_CONTEXT_MAX_CHARS: int = 12000

    RETRIEVAL_TOP_K: int = 5
    RETRIEVAL_SIMILARITY_THRESHOLD: float | None = 0.0

    # How many active memories may shape one answer, and how much of the chat
    # context the profile and those memories may occupy. The persona block is
    # taken out of CHAT_CONTEXT_MAX_CHARS rather than added to it, so adding a
    # Digital Twin cannot grow the prompt past the bound already in place.
    MAX_MEMORIES_IN_CONTEXT: int = 8
    PERSONA_CONTEXT_MAX_CHARS: int = 2000

    # --- OneDrive synchronisation ---------------------------------------
    #
    # Credentials for an Entra ID application using the client-credentials
    # flow. All three are required before any sync can run; leaving them unset
    # is the supported state for a deployment that does not sync, and the
    # application still imports and serves without them.
    ONEDRIVE_TENANT_ID: str | None = None
    ONEDRIVE_CLIENT_ID: str | None = None
    ONEDRIVE_CLIENT_SECRET: str | None = None

    # The drive every source belongs to unless it names its own. For a
    # SharePoint document library or a specific user's OneDrive, this is the
    # drive id from Graph.
    ONEDRIVE_DRIVE_ID: str | None = None

    # Which folders to synchronise, as a JSON array. Each entry needs a stable
    # `key` — it identifies the source in sync state and never changes — plus
    # either a `path` relative to the drive root or an explicit `item_id`.
    # Ids are preferred once known: a path breaks when somebody renames a
    # parent folder, and an id does not.
    #
    #   [{"key": "capabilities", "path": "Capabilities"},
    #    {"key": "case-studies", "item_id": "01ABCDEF...", "label": "Case Study"}]
    #
    # Empty means nothing is configured, which is not an error — it is the
    # state of every deployment that has not been given credentials yet.
    ONEDRIVE_SOURCES: str = ""

    ONEDRIVE_GRAPH_BASE_URL: str = "https://graph.microsoft.com/v1.0"
    ONEDRIVE_AUTHORITY: str = "https://login.microsoftonline.com"
    ONEDRIVE_REQUEST_TIMEOUT_SECONDS: float = 60.0

    # Files larger than this are reported and skipped rather than downloaded.
    # Defaults to the upload limit, so the same ceiling applies however a
    # document arrives.
    ONEDRIVE_MAX_FILE_BYTES: int | None = None

    # Where a file is written while it is being ingested. None means the
    # system temporary directory. Nothing is kept: the staging file is removed
    # whether ingestion succeeds or fails, so this is never a mirror of
    # OneDrive.
    ONEDRIVE_STAGING_DIR: str | None = None

    # How many files are downloaded at once, and how many are staged before
    # ingestion runs over them.
    #
    # Downloads are the only part of a sync that is safe to overlap: they touch
    # the network and the staging directory and nothing else. Parsing, chunking,
    # embedding and every database write stay on the calling thread with the one
    # session it was given — SQLAlchemy's `Session` is not thread-safe, and
    # sharing one across a pool is a data-corruption bug that only appears under
    # load.
    #
    # Four is deliberately modest. Graph throttles a first full sync hard, and
    # the failure mode of too much concurrency is a storm of 429s that makes the
    # run slower than doing it serially would have been.
    ONEDRIVE_DOWNLOAD_CONCURRENCY: int = 4

    # Files are downloaded in batches of this size and then ingested. The batch
    # bounds two things at once: how much staging disk is in use, and how long a
    # pre-authorised download URL sits unused before it is spent — Graph mints
    # those with a life of about an hour, so a run must never hold one for
    # longer than it takes to work through a batch.
    ONEDRIVE_DOWNLOAD_BATCH_SIZE: int = 16

    # Periodic incremental sync. Off by default — a background job that talks
    # to a third party and writes to the knowledge base should be switched on
    # deliberately, not inherited from a default.
    ONEDRIVE_SYNC_ENABLED: bool = False
    ONEDRIVE_SYNC_INTERVAL_SECONDS: int = 3600

    # --- Scheduled reports -----------------------------------------------
    #
    # Snapshotting each user's last *completed* week and month of email, so a
    # digest survives the provider ageing the messages out. Off by default for
    # the same reason the sync is: it reads a third party's mailbox on a timer.
    #
    # The interval is a *check*, not a schedule — the job asks whether the last
    # completed period has been recorded and does nothing if it has, so running
    # it hourly costs one query per user per type and no mailbox call. That is
    # what makes it safe to run often, and why there is no cron expression
    # here: the period boundaries are calendar arithmetic
    # (`services/features/reports/period.py`), not a crontab.
    REPORT_DIGEST_SCHEDULE_ENABLED: bool = False
    REPORT_DIGEST_INTERVAL_SECONDS: int = 3600

    # --- Administrator bootstrap -----------------------------------------
    #
    # `is_admin` guards exactly one operation — deleting another person's
    # Digital Twin — and no endpoint grants it, because one that promoted the
    # caller would make the check decorative. That leaves a deadlock: a
    # deployment with users and no administrator can never gain one.
    #
    # On startup, if nobody is an administrator, exactly one user is promoted
    # and the promotion is logged as a warning. This names which: the user with
    # this email address. Leave it empty and the earliest-created user — the
    # CEO, the first Digital Twin — is promoted instead. Nobody is ever
    # demoted, no second person is ever promoted, and no user is ever created.
    BOOTSTRAP_ADMIN_EMAIL: str | None = None

    # --- Email Agent -----------------------------------------------------
    #
    # Which mailbox provider to use, if any. Unset is the supported state: a
    # deployment with no mailbox still composes, rewrites, saves drafts and
    # manages templates — it simply cannot list an inbox or send. That is why
    # this is None rather than "outlook", and why nothing here has a default
    # that would make an unconfigured deployment *look* connected.
    #
    # "outlook" reuses the Entra application already configured for OneDrive
    # sync (ONEDRIVE_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET). One application,
    # one token cache, one client. Note that mail access is a *separate*
    # consent from files: Mail.Read and Mail.Send have to be granted to that
    # same application before any mailbox call succeeds.
    EMAIL_PROVIDER: str | None = None

    # The mailbox to act on, as a UPN or address. Application permissions are
    # tenant-wide and carry no user, so every Graph mail call has to name whose
    # mailbox it means.
    #
    # This is now a *fallback*, not the mailbox. Each user owns a `user_mailbox`
    # row (see app/models/email.py), and normal runtime behaviour is per-user.
    # This setting is only consulted for a user with no mailbox of their own,
    # and only when the flag below is switched on.
    EMAIL_MAILBOX_ADDRESS: str | None = None

    # Whether a user with no mailbox of their own falls back to
    # EMAIL_MAILBOX_ADDRESS. **Off by default, and deliberately so.** With it
    # on, every user of a multi-user deployment reads and sends from the same
    # inbox — which would look exactly like the feature working while leaking
    # one person's mail to everybody. Turn it on only for a genuinely
    # single-user or development deployment.
    EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX: bool = False

    # Attachments live in the database row (see app/models/email.py), so these
    # two are what keeps that decision defensible rather than a liability.
    EMAIL_MAX_ATTACHMENT_BYTES: int = 10 * 1024 * 1024
    EMAIL_MAX_ATTACHMENTS_PER_DRAFT: int = 10

    # How much retrieved company knowledge may be rendered into a generation
    # prompt, and how many passages to retrieve. Smaller than the chat budget:
    # an email is shorter than an answer, and a prompt stuffed with passages
    # produces a message that reads like a document summary.
    EMAIL_CONTEXT_MAX_CHARS: int = 6000
    EMAIL_RETRIEVAL_TOP_K: int = 4

    # How many messages one inbox page asks the provider for, and the most any
    # single request may ask for.
    #
    # Fifty rather than twenty-five because twenty-five is under a screenful for
    # anyone with real correspondence, and "Load more" on the second row of the
    # list is not a page. The ceiling exists so that a client cannot turn one
    # request into a mailbox crawl: Graph will happily serve a thousand, and a
    # request that large holds a worker open for as long as it takes.
    #
    # This is a page *size*, not a cursor. `EmailProvider.list_messages` takes a
    # count and returns a list, so "load more" re-asks for a larger page rather
    # than continuing from where the last one stopped. That is honest at these
    # sizes and stops being so above a few hundred; a real cursor is a change to
    # the provider contract and is recorded in docs/ROADMAP.md.
    EMAIL_INBOX_PAGE_SIZE: int = 50
    EMAIL_INBOX_MAX_PAGE_SIZE: int = 200

    # --- tasks and the weekly report -------------------------------------

    # How close a deadline has to be before a pending task is shown as
    # warning (amber). Compared against the due *timestamp*, not the date:
    # "within three days" is 72 hours, and a date comparison would answer
    # differently depending on the time of day it was asked.
    TASK_WARNING_DAYS: int = 3

    # How long an overdue task may sit before the weekly report asks for it to
    # be escalated to a person. Separate from the warning threshold because
    # they answer different questions: one is "this needs doing soon", the
    # other is "this is not getting done".
    TASK_ESCALATION_DAYS: int = 7

    # Who unresolved work is escalated to. Deliberately unset by default: with
    # nothing here the report says "Escalation target not identified" rather
    # than guessing a recipient, which is the one thing an escalation workflow
    # must never do.
    ESCALATION_CONTACT_ADDRESS: str | None = None
    ESCALATION_CONTACT_NAME: str | None = None

    LLM_PROVIDER: str = "openrouter"
    LLM_MODEL: str = "openai/gpt-oss-20b"
    OPENAI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None

    # How long one model call may take, and how many times the SDK may repeat
    # it. Both are set because the OpenAI client's defaults are wrong for a
    # request a person is waiting on: a 600-second read timeout and two
    # automatic retries mean a single unanswered call can hold a request for
    # half an hour, and a report makes a dozen of them in a row. Measured
    # against a stub provider that never answers: 18 calls became 54 HTTP
    # attempts, each able to wait ten minutes.
    # What the application's own loggers emit. Without this nothing configures
    # the root logger, so every INFO line this codebase writes — including the
    # timings and counts behind a report — was discarded, and the only trace a
    # slow report left was a bare warning with no context around it.
    LOG_LEVEL: str = "INFO"

    LLM_TIMEOUT_SECONDS: float = 45.0
    LLM_MAX_RETRIES: int = 1

    # The whole budget for the model calls behind one activity report. When it
    # is spent, the remaining conversations are described structurally and the
    # report says so in its header. This is what bounds the endpoint: without
    # it, response time is however long a provider feels like taking multiplied
    # by the number of conversations in the period.
    REPORT_ANALYSIS_BUDGET_SECONDS: float = 90.0

    # How long a stored activity report for a period that is still running is
    # served without checking the mailbox. A closed period is never rechecked
    # at all — it cannot change — so this governs the current week and month
    # only. Half an hour: long enough that opening the tab repeatedly costs
    # nothing, short enough that today's report is today's.
    ACTIVITY_REPORT_FRESH_SECONDS: int = 1800

    # How long one generation may hold its claim before another request may
    # take it over. Above the analysis budget plus a fetch, so a report that is
    # simply slow is never interrupted; bounded so a process that died holding
    # the claim does not make a window ungeneratable.
    ACTIVITY_REPORT_LOCK_SECONDS: int = 600

    # A shared secret the deployment's reverse proxy (Vercel) adds to every
    # request as `x-origin-secret`. When set, the API refuses any request that
    # does not carry it, so the backend's public URL cannot be called directly
    # around the proxy. Unset means no check, which is local development.
    # This is a perimeter, not authentication: `X-User-ID` is still trusted.
    ORIGIN_SECRET: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
