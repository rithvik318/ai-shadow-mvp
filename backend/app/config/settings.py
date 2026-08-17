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

    # Periodic incremental sync. Off by default — a background job that talks
    # to a third party and writes to the knowledge base should be switched on
    # deliberately, not inherited from a default.
    ONEDRIVE_SYNC_ENABLED: bool = False
    ONEDRIVE_SYNC_INTERVAL_SECONDS: int = 3600

    LLM_PROVIDER: str = "openrouter"
    LLM_MODEL: str = "openai/gpt-oss-20b"
    OPENAI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
