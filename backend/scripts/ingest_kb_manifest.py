"""Ingest the manifest's selected documents, then check what actually landed.

The manifest decides what belongs in the knowledge base; this sends exactly
that list to the existing `POST /documents/upload` and reports what happened to
each one. There is no second ingestion path here — no parsing, no chunking, no
embedding — because a script that reimplemented any of that would drift from
the pipeline it is supposed to be testing.

A failure on one document never stops the run, and never disappears: every
non-201 response is recorded with its status and body, and the exit code is
non-zero if anything failed.

Run from `backend/`, with the API up and the manifest built:

    uvicorn app.main:app                          # in another shell
    python -m scripts.ingest_kb_manifest
    python -m scripts.ingest_kb_manifest --verify-only
    python -m scripts.ingest_kb_manifest --limit 5 --dry-run

Writes `knowledge_base_ingestion_results.json` at the repository root.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.database.database import SessionLocal
from app.models.document import Document, DocumentChunk, DocumentStatus

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


def upload(base_url: str, path: Path, entry: dict) -> dict:
    """Send one document and record the outcome, whatever it is."""

    content_type = CONTENT_TYPES[path.suffix.lower()]
    started = time.perf_counter()

    try:
        with path.open("rb") as handle:
            response = httpx.post(
                f"{base_url}/documents/upload",
                files={"file": (path.name, handle, content_type)},
                timeout=900.0,
            )
    except httpx.HTTPError as error:
        return {
            "path": entry["path"],
            "filename": path.name,
            "category": entry["category"],
            "status_code": None,
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    elapsed = round((time.perf_counter() - started) * 1000)

    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:400]}

    ok = response.status_code == 201

    return {
        "path": entry["path"],
        "filename": path.name,
        "category": entry["category"],
        "brand": entry.get("brand"),
        "year": entry.get("year"),
        "status_code": response.status_code,
        "ok": ok,
        "document_id": body.get("id") if ok else None,
        "chunk_count": body.get("chunk_count") if ok else None,
        "page_count": body.get("page_count") if ok else None,
        "document_status": body.get("status") if ok else None,
        "error": None if ok else str(body.get("detail", body))[:400],
        "elapsed_ms": elapsed,
    }


def indexed_filenames(base_url: str) -> set[str]:
    response = httpx.get(
        f"{base_url}/documents",
        params={"status": "indexed", "limit": 200},
        timeout=60.0,
    )
    response.raise_for_status()

    return {item["filename"] for item in response.json()["items"]}


def verify(expected_filenames: set[str]) -> dict:
    """Check the database against what the manifest asked for.

    Reads through the application's own models rather than raw SQL, so this
    fails if the schema and the code have diverged — which is one of the things
    worth finding out.
    """

    with SessionLocal() as db:
        documents = db.execute(select(Document)).scalars().all()

        by_status: dict[str, int] = {}
        for document in documents:
            by_status[document.status.value] = (
                by_status.get(document.status.value, 0) + 1
            )

        indexed = [d for d in documents if d.status == DocumentStatus.INDEXED]

        chunk_rows = db.execute(
            select(
                DocumentChunk.document_id,
                func.count(DocumentChunk.id),
                func.count(DocumentChunk.embedding),
            ).group_by(DocumentChunk.document_id)
        ).all()
        chunks_by_document = {row[0]: (row[1], row[2]) for row in chunk_rows}

        indexed_without_chunks = [
            d.filename for d in indexed if chunks_by_document.get(d.id, (0, 0))[0] == 0
        ]
        chunks_without_embeddings = [
            d.filename
            for d in indexed
            if chunks_by_document.get(d.id, (0, 0))[0]
            != chunks_by_document.get(d.id, (0, 0))[1]
        ]
        count_disagrees = [
            d.filename
            for d in indexed
            if chunks_by_document.get(d.id, (0, 0))[0] != d.chunk_count
        ]

        orphans = db.execute(
            select(func.count())
            .select_from(DocumentChunk)
            .outerjoin(Document, Document.id == DocumentChunk.document_id)
            .where(Document.id.is_(None))
        ).scalar_one()

        present = {d.filename for d in documents}

        return {
            "documents_in_database": len(documents),
            "documents_by_status": by_status,
            "total_chunks": sum(count for count, _ in chunks_by_document.values()),
            "orphan_chunks": orphans,
            "indexed_without_chunks": sorted(indexed_without_chunks),
            "chunks_missing_embeddings": sorted(chunks_without_embeddings),
            "chunk_count_disagrees_with_rows": sorted(count_disagrees),
            "expected_but_absent": sorted(expected_filenames - present),
            "present_but_not_in_manifest": sorted(present - expected_filenames),
        }


def print_verification(report: dict) -> None:
    print(f"  documents in database    : {report['documents_in_database']}")
    print(f"  by status                : {report['documents_by_status']}")
    print(f"  total chunks             : {report['total_chunks']}")
    print(f"  orphan chunks            : {report['orphan_chunks']}")

    for label, key in (
        ("indexed with no chunks", "indexed_without_chunks"),
        ("chunks missing embeddings", "chunks_missing_embeddings"),
        ("chunk_count disagrees", "chunk_count_disagrees_with_rows"),
        ("selected but absent", "expected_but_absent"),
        ("present, not in manifest", "present_but_not_in_manifest"),
    ):
        entries = report[key]
        print(f"  {label:<25}: {len(entries)}")
        for name in entries[:10]:
            print(f"      - {name[:70]}")
        if len(entries) > 10:
            print(f"      ... and {len(entries) - 10} more")


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", default=str(repository_root / "knowledge_base_manifest.json")
    )
    parser.add_argument(
        "--out", default=str(repository_root / "knowledge_base_ingestion_results.json")
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--corpus",
        default=None,
        help="override the corpus root recorded in the manifest",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="ingest only the first N"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would be sent, send nothing"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="skip ingestion and only check the database against the manifest",
    )
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="upload even documents whose filename is already indexed",
    )
    arguments = parser.parse_args()

    manifest_path = Path(arguments.manifest)
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        print("run: python -m scripts.build_kb_manifest --corpus ...", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    corpus = Path(arguments.corpus or manifest["corpus_root"])
    selected = manifest["include"]
    if arguments.limit is not None:
        selected = selected[: arguments.limit]

    expected = {Path(entry["path"]).name for entry in selected}

    if arguments.verify_only:
        print("=" * 78)
        print("DATABASE VERIFICATION")
        print("=" * 78)
        report = verify(expected)
        print_verification(report)
        return 0

    print("=" * 78)
    print(f"KNOWLEDGE BASE INGESTION — {len(selected)} selected documents")
    print("=" * 78)

    already = set() if arguments.reingest else indexed_filenames(arguments.base_url)

    results: list[dict] = []
    skipped: list[dict] = []
    missing: list[dict] = []
    started = time.perf_counter()

    for index, entry in enumerate(selected, start=1):
        path = corpus / entry["path"]

        if not path.is_file():
            missing.append({"path": entry["path"], "reason": "not found on disk"})
            print(f"  {index:>3}/{len(selected)}  MISSING  {entry['path'][:64]}")
            continue

        if path.name in already:
            skipped.append({"path": entry["path"], "reason": "already indexed"})
            print(f"  {index:>3}/{len(selected)}  skip     {path.name[:64]}")
            continue

        if arguments.dry_run:
            print(f"  {index:>3}/{len(selected)}  would    {path.name[:64]}")
            continue

        result = upload(arguments.base_url, path, entry)
        results.append(result)

        marker = "ok " if result["ok"] else "FAIL"
        chunks = result.get("chunk_count")
        print(
            f"  {index:>3}/{len(selected)}  {marker}  "
            f"{str(result['status_code'] or '-'):>4}  "
            f"chunks={str(chunks if chunks is not None else '-'):<5} "
            f"{result['elapsed_ms']:>7} ms  {path.name[:52]}"
        )
        if not result["ok"]:
            print(f"          -> {result['error']}")

    elapsed = time.perf_counter() - started

    if arguments.dry_run:
        print(f"\n  dry run: {len(selected)} documents, nothing sent")
        return 0

    successful = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    total_chunks = sum(r["chunk_count"] or 0 for r in successful)

    print()
    print("=" * 78)
    print("DATABASE VERIFICATION")
    print("=" * 78)
    verification = verify(expected)
    print_verification(verification)

    report = {
        "manifest": str(manifest_path),
        "corpus_root": manifest["corpus_root"],
        "summary": {
            "total_selected": len(selected),
            "successful": len(successful),
            "failed": len(failed),
            "skipped": len(skipped),
            "missing_on_disk": len(missing),
            "chunks_created": total_chunks,
            "elapsed_seconds": round(elapsed, 1),
        },
        "failures_by_filename": {r["filename"]: r["error"] for r in failed},
        "skipped": skipped,
        "missing_on_disk": missing,
        "verification": verification,
        "documents": results,
    }

    Path(arguments.out).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for key, value in report["summary"].items():
        print(f"  {key:<20}: {value}")
    print(f"\n  wrote {arguments.out}")

    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
