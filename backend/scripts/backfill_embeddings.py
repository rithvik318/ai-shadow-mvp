"""Generate embeddings for chunks that have none.

Documents ingested before embedding generation existed have chunks but no
vectors, which makes them invisible to retrieval. So do documents whose
embedding step failed at upload time. This backfills both.

    cd backend && python -m scripts.backfill_embeddings [--limit N]

Safe to re-run: chunks that already have a vector are skipped.
"""

import argparse
import logging
import sys

from app.database.database import SessionLocal
from app.services.features.documents.indexing_service import (
    backfill_missing_embeddings,
    count_unembedded_chunks,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of documents to process in this run.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = SessionLocal()
    try:
        outstanding = count_unembedded_chunks(db)
        print(f"chunks without an embedding: {outstanding}")

        if not outstanding:
            return 0

        result = backfill_missing_embeddings(db, limit=args.limit)
    finally:
        db.close()

    print(
        f"documents processed: {result.documents_processed}\n"
        f"documents failed:    {result.documents_failed}\n"
        f"chunks embedded:     {result.chunks_embedded}"
    )

    return 1 if result.documents_failed else 0


if __name__ == "__main__":
    sys.exit(main())
