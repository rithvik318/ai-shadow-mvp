# AI Shadow MVP — Project State

A checkpoint, not a history. Read this first in a new session: it says where the
project stands, so nothing has to be reconstructed from old conversations. What
exists, in detail, is [`FEATURES.md`](FEATURES.md); how work gets done here is
[`../CLAUDE.md`](../CLAUDE.md).

**Checkpoint:** built on `d15588e` (`main`). Ingestion work is in the tree,
uncommitted and not yet run against the suite. **Updated:** 2026-08-16.

---

## Current phase

Backend feature build. Retrieval, RAG chat, the multi-user Digital Twin and the
document ingestion / knowledge base foundation are done. The next component is
**OneDrive synchronization**, which will call the ingestion service that now
exists rather than adding one of its own.

## Completed

- **Documents** — parse (PDF, DOCX, TXT, Markdown) with page and heading provenance, chunk with overlap, embed, index. Upload, list, retrieve, delete.
- **Retrieval** — top-k cosine search over pgvector with a configurable `top_k` and similarity floor, deduplicated, restricted to `indexed` documents.
- **Chat and search** — `POST /chat` answers from retrieved passages with `[SOURCE n]` citations; `POST /search` returns the same ranked passages without calling the model.
- **Digital Twin** — one profile and a typed memory store (`fact`, `preference`, `decision`, `commitment`, `context`), written explicitly through the API and composed above the retrieved passages as persona context.
- **Multi-user Digital Twin** — `users` table, `X-User-ID` identity, migration `0003`, user-scoped profile, memory and chat. Implemented and stabilized.
- **Knowledge base** — a deterministic manifest selects the corpus and records why for every file; a second script ingests exactly that list through the upload endpoint. The manifest milestone is committed.
- **Multi-file upload** — `POST /documents/batch-upload`, per-file results, failures isolated to the file that caused them. Both upload paths share one ingestion service.
- **Idempotent ingestion and re-indexing** — identity is a content hash plus an optional source identifier; unchanged files are skipped, changed ones re-indexed in place with their old chunks removed.

## Test status

**468 passing at `d15588e`.** The ingestion work adds 54 tests and has **not been
run** — it was written in an environment with no Python dependencies available.
`ruff check` and `ruff format --check` are clean on the full tree. Run `pytest -q`
from `backend/` before treating this component as done. The suite runs on
in-memory SQLite with no network, no credentials and no running services — keep
it that way.

## Architecture boundaries

- **The company knowledge base is shared.** `documents.user_id` and
  `document_chunks.user_id` are the string `MVP_USER_ID` for everyone. The corpus
  is company-wide and is deliberately not partitioned per person.
- **The Digital Twin is private.** `digital_twin_profile.user_id` and
  `digital_twin_memory.user_id` are UUID foreign keys into `users`, and every
  query filters on them. These are two different owner concepts; merging them
  would either leak one person's memories or fragment the shared corpus.
- **Persona shapes, documents cite.** Profile and memory influence tone, emphasis
  and priorities. Only retrieved passages may be cited as `[SOURCE n]`, and
  `sources` in the response is built from retrieved chunks alone.
- **`X-User-ID` is identity, not authentication.** Missing → `401`, malformed →
  `422`, unknown → `404`. It never creates a user, and it is trusted as sent.
- **Layering holds.** API → feature services → engines → LLM layer → persistence.
  Services raise domain errors from `app/core/exceptions.py`; only `app/main.py`
  knows status codes. Prompts are registered templates, never inline strings.
- **One ingestion pipeline.** Single upload, batch upload and — later — OneDrive
  sync all call `ingestion_service`. Adding a caller must not add a pipeline.
- **Only `indexed` documents are retrievable.** `failed` and `unsupported`
  documents are unreachable from `/search` and `/chat` whatever chunks they
  still hold, so a partial ingest never answers a question.

## Current task

None in progress. The ingestion component is written and awaiting its first
suite run.

## Next planned components

1. **OneDrive synchronization** — Microsoft Graph, change discovery, and calling
   the existing ingestion service with `source_uri` and `source_version`.
2. **Frontend** — React and Tailwind, after that.

## Known limitations

- No authentication. `X-User-ID` is trusted exactly as sent.
- Ingestion is synchronous and runs on the request thread, embedding round trip included.
- Legacy formats — `.doc`, `.ppt`, `.vsd`, spreadsheets — are reported rather than parsed, and are the largest gap in corpus coverage. No conversion layer was built.
- The 231 documents already in the knowledge base carry `content_hash = NULL`, so they do not participate in deduplication. They cannot be backfilled: the original bytes were never stored. `scripts/ingest_kb_manifest.py` still skips by filename, so re-running it does not duplicate them; a document acquires a real identity the next time it is uploaded.
- `source_uri` and `source_version` cannot be set through the HTTP API. They are service-level parameters, waiting for the sync that will supply them.
- The retrieval similarity floor still defaults to `0.0` and has not been tuned against real documents.
- No CI, no type checking in CI, no structured logging, no upload rate limiting.

Detail and priority live in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

## Explicitly deferred

Autonomous agents, email, CRM, calendar, LangGraph and n8n — none of them, in any
partial form. Also deferred, from the reference repository's design: the AI
Orchestrator, the tool architecture and multi-agent workflows. Nothing here is
built until something concretely requires it.
