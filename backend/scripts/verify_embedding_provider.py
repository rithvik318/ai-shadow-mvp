"""One live call against the configured embedding provider.

Answers the question the code cannot answer on its own: does this provider
actually serve embeddings through the OpenAI SDK, and at the width this
schema expects?

    cd backend && python -m scripts.verify_embedding_provider

Exits 0 on success, 1 on failure. Prints the provider, model and dimensions so
a mismatch is obvious before any document is ingested.
"""

import sys
import time

from app.config.settings import settings
from app.core.exceptions import EmbeddingError
from app.services.llm.embedding_service import embedding_service


def main() -> int:
    provider = settings.EMBEDDING_PROVIDER or settings.LLM_PROVIDER

    print(f"provider:   {provider}")
    print(f"model:      {settings.EMBEDDING_MODEL}")
    print(f"expects:    {settings.EMBEDDING_DIMENSIONS} dimensions")
    print("calling provider...")

    started = time.perf_counter()

    try:
        vector = embedding_service.embed_query("connectivity check")
    except EmbeddingError as exc:
        print(f"\nFAILED: {exc}")
        print(
            "\nIf this provider does not serve embeddings, set "
            "EMBEDDING_PROVIDER=openai and EMBEDDING_MODEL=text-embedding-3-small "
            "in backend/.env. No code change is required."
        )
        return 1

    duration_ms = (time.perf_counter() - started) * 1000

    print(f"\nOK: received {len(vector)} dimensions in {duration_ms:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
