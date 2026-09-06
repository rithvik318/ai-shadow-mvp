# AI Shadow MVP — Project State

A checkpoint, not a history. Read this first in a new session: it says where the
project stands, so nothing has to be reconstructed from old conversations. What
exists, in detail, is [`FEATURES.md`](FEATURES.md); how work gets done here is
[`../CLAUDE.md`](../CLAUDE.md).

**Checkpoint:** built on `0ff57a3` (`main`), plus the tasks/mailbox milestone,
the OneDrive → Knowledge Base milestone and the reports/digests pass, all
uncommitted in the tree. The frontend installs, lints, typechecks, tests and
builds with its real dependencies. **Updated:** 2026-09-02.

The reports pass added stored report history (`generated_report`, migration
`0012`), the weekly and monthly email digests, a schedule for them reusing
`SyncScheduler`, the Reports workspace in the frontend, mailbox connection UX,
a "create tasks from follow-ups" action, and the administrator bootstrap that
resolves the `is_admin` deadlock. The weekly-report frontend, outstanding from
the previous milestone, is now in place and is the interactive view of the week
in progress.

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

**Backend: 1055 passed, 4 skipped**, actually executed with `pytest -q` from
`backend/` — dependencies now install here, so these numbers are measured
rather than projected. `ruff check` and `ruff format --check` are clean across
`app tests scripts alembic`, and `python -m scripts.static_check` resolves every
first-party import and checks every first-party call site against its signature.

Note this figure describes **this tree**. A working copy without the
tasks/mailbox milestone applied sits at a different total, so a mismatch is a
sign the archives were not extracted rather than a sign of a regression.

**Frontend: 134 passing**, actually executed — `node --import tsx --test` over
`src/tests/`, covering the API client (endpoint paths, `X-User-ID`
propagation, error translation) and the upload result mapping. They need no
browser and no bundler. The React components have **never been built or
typechecked against real `@types/react`**: the environment they were written in
had no package registry. `npm install && npm run lint && npm run typecheck && npm run test && npm run
build` have now all been run and are green. `npm run format:check` still reports
eight pre-existing files from the Email Agent milestone; they were left alone
rather than reformatted inside an unrelated change.

## Assumptions made where the brief was ambiguous

Recorded here rather than re-asked, per `CLAUDE.md` §2. Each is a configurable
default, and each can be changed without touching a service.

- **`OVERDUE` and `ESCALATION_REQUIRED` are display states, not stored ones.**
  The requested vocabulary lists six task statuses. Four of them — `todo`,
  `in_progress`, `completed`, `blocked` — are a lifecycle a person controls and
  are stored (`cancelled` is kept as a fifth: abandoned is not the same as
  done). The other two are functions of the clock and the escalation rules, so
  a stored copy is wrong from the moment it is written until something rewrites
  it. They are computed on read into `display_status`, and the API sends both.
- **Escalation outranks overdue in `display_status`.** An overdue task tells
  its owner to get on with it; an escalated one tells them it is no longer
  theirs alone to finish, which is the louder instruction.
- **Green covers "completed" and "comfortably within the deadline".** Yellow is
  `TASK_WARNING_DAYS` (default 3) or fewer remaining; red is overdue, blocked,
  or escalation-required. All three thresholds are settings.
- **The escalation target comes from configuration only.** With
  `ESCALATION_CONTACT_ADDRESS` unset the report reads "Escalation target not
  identified". The counterparty on the task is deliberately not a fallback:
  they are usually the person who has not replied, and escalating to them is
  another follow-up wearing a different hat.
- **A past meeting nobody has answered for reads `unknown`.** It stays
  `scheduled` in the row — that is what was recorded — and reads as `unknown`,
  because that is what is known. Sweeping the transition into the database
  would need a background job and would be wrong for the window between the
  meeting ending and the sweep running.
- **Recipient display names are not stored.** `normalise_recipient_values`
  reduces `"Name <addr>"` to `addr`, which is what a provider is handed.
  Keeping a parallel list of names would be a schema change nothing reads.
  Sender names, where they do matter, are structural on `EmailAssessment`
  (`sender_name` / `sender_address`), and organiser names on `CalendarEvent`.
- **The report's window looks seven days back and seven forward.** Forward-only
  would never surface the meeting nobody has answered for.
- **Deletion requires typing the twin's name.** The act is irreversible; one
  click is not enough ceremony for it.

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
  call `ingestion_service`. Adding a caller must not add a pipeline. There is
  likewise one retriever: a synced document and an uploaded one are
  indistinguishable to `/search` and `/chat`.
- **One mailbox per person, resolved from the caller.** `user_mailbox` holds
  it; `mailbox_config_service.provider_for(user_id)` is the only way a provider
  is built for a user, and it refuses rather than falling back.
  `EMAIL_MAILBOX_ADDRESS` is a single-user fallback that is **off** unless
  `EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX` is deliberately set, so no user can
  silently act as another.
- **Every user-owned table joins `_OWNED_MODELS`.** A new private table that
  does not is one whose rows outlive the person they describe;
  `test_a_deleted_users_events_go_with_them` is what makes forgetting fail.
- **Derived states are never written back.** `overdue`, `escalation_required`
  and an unanswered meeting's `unknown` are all computed on read.
- **No folder is named in code.** `ONEDRIVE_SOURCES` is the only place a
  OneDrive folder appears, so a sixth source is an environment change. A source
  carries a stable `key`, an optional human-facing `uri` and an `enabled` flag;
  disabling one keeps the delta token stored against its key.
- **Graph ids come from Graph.** `source_resolver` asks Graph where a path is.
  Nothing decodes an id out of a sharing URL, which is how these integrations
  usually end up pointed confidently at the wrong folder.
- **Status is composed, not duplicated.** `GET /sync/onedrive/status` owns
  per-source facts; `GET /documents/stats` owns corpus totals, counted in the
  database. There is deliberately no aggregate `/kb/status` restating both.
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

None in progress. The OneDrive → Knowledge Base pipeline is complete and tested
end to end against a mock Graph: configuration, resolution, ingestion, delta
sync, scheduling, status, the Knowledge Base screen and retrieval with
attribution.

**No real OneDrive tenant has been synchronised from this code.** The owner
reports that token acquisition, `Files.Read.All` consent and drive visibility
all work from their machine; that verification is theirs, not this
repository's. `ONEDRIVE_SOURCES` is still empty here, so the five SunRadia
folders have never been resolved to real ids and nothing has been downloaded.
The next step is `python -m scripts.discover_onedrive_sources --resolve` on a
machine holding the credentials.

No mailbox is connected: `EMAIL_PROVIDER` is unset, and the `Mail.Read` /
`Mail.Send` consents have not been granted to the Entra application.

## Next planned components

1. **First real OneDrive sync.** Fill `ONEDRIVE_TENANT_ID`,
   `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET` and `ONEDRIVE_DRIVE_ID`, name
   the five folders by path in `ONEDRIVE_SOURCES`, run
   `python -m scripts.discover_onedrive_sources --resolve` and paste back the
   pinned line, then `POST /sync/onedrive` with one source before all five.
2. **Connect the mailbox.** Grant `Mail.Read` and `Mail.Send` to the existing
   Entra application, set `EMAIL_PROVIDER=outlook` and `EMAIL_MAILBOX_ADDRESS`,
   and check `GET /email/provider/status`. That endpoint reports exactly what is
   missing, and nothing in the Email Agent needs to change when it goes green.
3. **Hardening** — authentication behind `X-User-ID`, CI, background ingestion,
   and CORS or same-origin serving for the deployed frontend.

## Known limitations

- No authentication. `X-User-ID` is trusted exactly as sent.
- Ingestion is synchronous and runs on the request thread, embedding round trip included.
- Legacy formats — `.doc`, `.ppt`, `.vsd`, spreadsheets — are reported rather than parsed, and are the largest gap in corpus coverage. No conversion layer was built.
- The 231 documents already in the knowledge base carry `content_hash = NULL`, so they do not participate in deduplication. They cannot be backfilled: the original bytes were never stored. `scripts/ingest_kb_manifest.py` still skips by filename, so re-running it does not duplicate them; a document acquires a real identity the next time it is uploaded.
- `source_uri` and `source_version` cannot be set through the HTTP upload API. They are service-level parameters, and OneDrive sync is what supplies them.
- OneDrive sync has never run against a real tenant from this repository. Credentials, a drive id and the five folder paths are all still to be supplied here, and Graph's real payloads may differ from the mocked ones in ways only a live run will show.
- Sync state stores the counts of the *last* run per source, not a per-source document total. Corpus size comes from `GET /documents/stats`.
- The weekly-report frontend and the documentation for the tasks/mailbox milestone remain outstanding from that milestone; this one did not address them.
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
