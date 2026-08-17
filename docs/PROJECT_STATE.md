# AI Shadow MVP — Project State

A checkpoint, not a history. Read this first in a new session: it says where the
project stands, so nothing has to be reconstructed from old conversations. What
exists, in detail, is [`FEATURES.md`](FEATURES.md); how work gets done here is
[`../CLAUDE.md`](../CLAUDE.md).

**Checkpoint:** built on `0ff57a3` (`main`). The frontend is in the tree,
uncommitted; it has never been installed or built. **Updated:** 2026-08-17.

---

## Current phase

Feature-complete end to end, backend and frontend. Retrieval, RAG chat, the
multi-user Digital Twin, ingestion, OneDrive synchronization and a React chat
workspace are all built. The next phase is **hardening**.

## Completed

- **Documents** — parse (PDF, DOCX, TXT, Markdown) with page and heading provenance, chunk with overlap, embed, index. Upload, list, retrieve, delete.
- **Retrieval** — top-k cosine search over pgvector with a configurable `top_k` and similarity floor, deduplicated, restricted to `indexed` documents.
- **Chat and search** — `POST /chat` answers from retrieved passages with `[SOURCE n]` citations; `POST /search` returns the same ranked passages without calling the model.
- **Digital Twin** — one profile and a typed memory store (`fact`, `preference`, `decision`, `commitment`, `context`), written explicitly through the API and composed above the retrieved passages as persona context.
- **Multi-user Digital Twin** — `users` table, `X-User-ID` identity, migration `0003`, user-scoped profile, memory and chat. Implemented and stabilized.
- **Knowledge base** — a deterministic manifest selects the corpus and records why for every file; a second script ingests exactly that list through the upload endpoint. The manifest milestone is committed.
- **Multi-file upload** — `POST /documents/batch-upload`, per-file results, failures isolated to the file that caused them. Both upload paths share one ingestion service.
- **Idempotent ingestion and re-indexing** — identity is a content hash plus an optional source identifier; unchanged files are skipped, changed ones re-indexed in place with their old chunks removed.
- **OneDrive synchronization** — Microsoft Graph client-credentials auth, configurable source folders, delta-based incremental runs, deletion handling, per-file results, and an optional periodic run. Calls the existing ingestion service.
- **Frontend** — `frontend/`, React + Tailwind + Vite. Chat workspace with in-composer multi-file upload, per-file ingestion outcomes, knowledge-base panel, Digital Twin panel, and citation cards kept separate from the answer.

## Test status

**Backend: 637 expected** (632 at `0ff57a3` plus the 5 sync regression tests).
Not re-run since; run `pytest -q` from `backend/`.

**Frontend: 37 passing**, actually executed — `node --import tsx --test` over
`src/tests/`, covering the API client (endpoint paths, `X-User-ID`
propagation, error translation) and the upload result mapping. They need no
browser and no bundler. The React components have **never been built or
typechecked** against real `@types/react`: the environment they were written in
had no package registry. Run `npm install && npm run typecheck && npm run
build` in `frontend/` before treating the UI as working.

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
- **One ingestion pipeline.** Single upload, batch upload and OneDrive sync all
  call `ingestion_service`. Adding a caller must not add a pipeline.
- **Graph stays in `app/services/graph/`.** Nothing below the sync service knows
  what a document is; nothing above it knows what a bearer token is.
- **The delta token is only advanced when it is safe.** A transient failure —
  a download that never completed — keeps the previous token and reports
  `partial`, so the next run re-examines that window. A parse failure does not,
  because it will fail identically forever.
- **The frontend adds no state.** Every document, profile, memory and answer
  comes from the API. Conversations are in-memory only, because `/chat` is
  stateless and persisting a transcript would imply a continuity the answers do
  not have.
- **The user selector is identity, not sign-in**, and the UI says so where a
  person can read it.
- **Only `indexed` documents are retrievable.** `failed` and `unsupported`
  documents are unreachable from `/search` and `/chat` whatever chunks they
  still hold, so a partial ingest never answers a question.

## Current task

None in progress. The frontend awaits its first `npm install` and build. No real
OneDrive tenant has been configured — `ONEDRIVE_SOURCES` is empty, and the five
SunRadia folders are not yet pointed at anything.

## Next planned components

1. **Hardening** — authentication behind `X-User-ID`, CI, background ingestion,
   and CORS or same-origin serving for the deployed frontend.
2. **Surfacing what exists** — `POST /search`, profile and memory editing, and
   a OneDrive sync status page. All wrapped in the frontend API layer, none
   given a screen.

## Known limitations

- No authentication. `X-User-ID` is trusted exactly as sent.
- Ingestion is synchronous and runs on the request thread, embedding round trip included.
- Legacy formats — `.doc`, `.ppt`, `.vsd`, spreadsheets — are reported rather than parsed, and are the largest gap in corpus coverage. No conversion layer was built.
- The 231 documents already in the knowledge base carry `content_hash = NULL`, so they do not participate in deduplication. They cannot be backfilled: the original bytes were never stored. `scripts/ingest_kb_manifest.py` still skips by filename, so re-running it does not duplicate them; a document acquires a real identity the next time it is uploaded.
- `source_uri` and `source_version` cannot be set through the HTTP upload API. They are service-level parameters, and OneDrive sync is what supplies them.
- OneDrive sync has never run against a real tenant. Credentials, a drive id and the five folder paths are all still to be supplied, and Graph's real payloads may differ from the mocked ones in ways only a live run will show.
- Graph permissions are tenant-wide, the scheduled run assumes one worker process, and a sync holds its request open for the whole folder. All three are in `KNOWN_ISSUES.md`.
- The backend registers no CORS middleware, so the frontend is same-origin only: proxied in development, and expected to be served alongside the API in production.
- Chat history is not persisted anywhere, and the frontend has no profile or memory editing UI.
- The retrieval similarity floor still defaults to `0.0` and has not been tuned against real documents.
- No CI, no type checking in CI, no structured logging, no upload rate limiting.

Detail and priority live in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

## Explicitly deferred

Autonomous agents, email, CRM, calendar, LangGraph and n8n — none of them, in any
partial form. Also deferred, from the reference repository's design: the AI
Orchestrator, the tool architecture and multi-agent workflows. Nothing here is
built until something concretely requires it.
