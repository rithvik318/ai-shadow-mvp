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
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 150

    EMBEDDING_DIMENSIONS: int = 1536

    LLM_PROVIDER: str = "openrouter"
    LLM_MODEL: str = "openai/gpt-oss-20b"
    OPENAI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
print("=" * 80)
print("DATABASE_URL =", settings.DATABASE_URL)
print("=" * 80)
