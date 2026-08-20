# AI Shadow MVP — Project State

A checkpoint, not a history. Read this first in a new session: it says where the
project stands, so nothing has to be reconstructed from old conversations. What
exists, in detail, is [`FEATURES.md`](FEATURES.md); how work gets done here is
[`../CLAUDE.md`](../CLAUDE.md).

**Checkpoint:** built on `0ff57a3` (`main`). The frontend and the Email Agent
are in the tree, uncommitted. The frontend has never been installed or built
with its real dependencies. **Updated:** 2026-08-19.

---

## Current phase

Feature-complete end to end, backend and frontend. Retrieval, RAG chat, the
multi-user Digital Twin, ingestion, OneDrive synchronization, a React workspace
and the **Email Agent** are all built. Two things remain unverified against the
real world, and both are external rather than structural: no OneDrive folder has
been synchronised from a live tenant, and no mailbox has been connected.

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
- **Frontend** — `frontend/`, React + Tailwind + Vite. Chat workspace with in-composer multi-file upload, per-file ingestion outcomes, knowledge-base panel, Digital Twin panel, Email Agent workspace, and citation cards kept separate from the answer.
- **Email Agent** — drafting, ten revision operations, user-owned templates with placeholder filling, drafts with attachments, triage, follow-up recommendations, and a provider boundary with an Outlook implementation over the existing Graph client. Migration `0006`. Human approval is required before any send, and editing a draft withdraws it.

## Test status

**Backend: 650 at the last local run** (637 plus 13 openapi/graph tests), and
the Email Agent adds **roughly 150 more**, not yet executed — this box has no
package registry, so `pytest` could not be run here. Run `pytest -q` from
`backend/`. What *was* run: `ruff check`, `ruff format --check`, `py_compile`,
and `python -m scripts.static_check`, which resolves every first-party import
and checks every first-party call site against its signature.

**Frontend: 123 passing**, actually executed — `node --import tsx --test` over
`src/tests/`, covering the API client (endpoint paths, `X-User-ID`
propagation, error translation) and the upload result mapping. They need no
browser and no bundler. The React components have **never been built or
typechecked against real `@types/react`**: the environment they were written in
had no package registry. They *were* typechecked against a hand-written minimal
`@types/react` shim, which is clean across the whole tree and which caught two
real defects in the email components. That is a checking aid, not a substitute.
Run `npm install && npm run lint && npm run typecheck && npm run test && npm run
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
- **One sync per source at a time.** The claim is written to
  `onedrive_sync_state.status`, not held in process memory, because the
  scheduler and a manual API call may not be the same process. A run older
  than six hours is treated as stale so a crash cannot block syncing forever.
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
- **The Email Agent never sends on its own account.** A draft leaves only if a
  person called `POST /email/drafts/{id}/approve` and then `/send`, and **any
  edit to a draft withdraws the approval**. No prompt, no service and no route
  gives a model a path to a provider.
- **There is no simulated mailbox.** No null provider, no in-memory provider, no
  demo inbox. With nothing configured, sending is `409` and the inbox is `409` —
  "no mail" and "no mailbox" are shown as the different facts they are, and a
  draft reading `sent` always means a provider confirmed it.
- **The Email Agent does not know what Graph is.** Everything above
  `app/services/email/provider/base.py` speaks in provider-neutral dataclasses;
  the Outlook module is the only file that names Microsoft, and it reuses the
  existing `GraphClient` rather than authenticating again.
- **Email timestamps are aware on every dialect.** The email tables use
  `UtcDateTime` (`app/database/types.py`), a `TypeDecorator` that converts to
  UTC on write and labels UTC on read. `DateTime(timezone=True)` alone is a
  no-op on SQLite, which returns naive values — so "is this follow-up overdue?"
  raised `TypeError` under test and not in production, and an offset-bearing
  value could be stored with its clock reading rather than its instant. Only
  the email tables use it; retrofitting the others is a separate change.
- **A mailbox is not mirrored.** There is no message table. Only what triage
  *concluded* is stored, keyed by the provider's own message id, plus the
  subject, sender and timestamp needed to recognise a row. Bodies are not kept.
- **Only `indexed` documents are retrievable.** `failed` and `unsupported`
  documents are unreachable from `/search` and `/chat` whatever chunks they
  still hold, so a partial ingest never answers a question.

## Current task

None in progress. The frontend awaits its first `npm install` and build. No real
OneDrive tenant has been synchronised — `ONEDRIVE_SOURCES` is empty, and the
five SunRadia folders are not yet pointed at anything. No mailbox is connected:
`EMAIL_PROVIDER` is unset, and the `Mail.Read` / `Mail.Send` consents have not
been granted to the Entra application.

## Next planned components

1. **Run the suites.** `pytest -q` in `backend/`, and `npm install` then lint,
   typecheck, test and build in `frontend/`. Nothing else should be built until
   both are green.
2. **Connect the mailbox.** Grant `Mail.Read` and `Mail.Send` to the existing
   Entra application, set `EMAIL_PROVIDER=outlook` and `EMAIL_MAILBOX_ADDRESS`,
   and check `GET /email/provider/status`. That endpoint reports exactly what is
   missing, and nothing in the Email Agent needs to change when it goes green.
3. **First real OneDrive sync**, still outstanding from the previous milestone.
4. **Hardening** — authentication behind `X-User-ID`, CI, background ingestion,
   and CORS or same-origin serving for the deployed frontend.

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
- No mailbox has ever been connected. Every Outlook behaviour is verified against a mock transport only; a live tenant may differ, and nothing in this repository proves otherwise.
- Email attachment bytes are stored in the database row. Bounded by configuration and deliberate for this scale; a blob store is the answer when it stops being.
- Triage runs one message at a time, on request, because each assessment is a model call. There is no bulk triage and no background pass.
- The retrieval similarity floor still defaults to `0.0` and has not been tuned against real documents.
- No CI, no type checking in CI, no structured logging, no upload rate limiting.

Detail and priority live in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

## Explicitly deferred

Autonomous agents, CRM, calendar, LangGraph and n8n — none of them, in any
partial form. Email is now built, but everything autonomous about it is still
deferred: nothing schedules a send, nothing contacts anybody without a person
approving it, and no model can reach a mailbox provider. Also deferred, from the reference repository's design: the AI
Orchestrator, the tool architecture and multi-agent workflows. Nothing here is
built until something concretely requires it.
