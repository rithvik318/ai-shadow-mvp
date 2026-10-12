# AI Shadow MVP — Features Catalog

The authoritative answer to "does X exist today". Where this document and [`ARCHITECTURE.md`](ARCHITECTURE.md) disagree, this document wins. For phase sequencing see [`ROADMAP.md`](ROADMAP.md).

---

## Implemented

### Document Upload & Ingestion
Upload a PDF, DOCX, PPTX, XLSX, HTML, TXT or Markdown file; it is validated, its text extracted with page and heading provenance, split into overlapping chunks, embedded, and persisted. `POST /documents/upload`. A document reaches `indexed` only once every chunk carries a vector.

HTML is read with the standard library's parser — `script` and `style` are dropped, because indexing a page's own JavaScript makes every page on a site resemble every other — and split on its headings. A workbook becomes one section per worksheet, rows rendered as `Header: value` so a row separated from its header row by a chunk boundary still says what its numbers mean, capped at `MAX_SHEET_ROWS` because a 50,000-row sheet embedded whole is a retrieval result that matches everything.

`documents.file_size_bytes` is `BIGINT` (migration 0014). It records the size a file *reported*, which for a rejected synchronised file is bounded by the drive rather than by `MAX_UPLOAD_SIZE_BYTES` — a 6.8 GB Teams recording overflowed `INTEGER` and aborted a whole sync run. The value is stored whole; clamping it would be a lie in the one column whose purpose is to say how big the unreadable thing was.

A format we recognise and deliberately refuse — `.doc`, `.xls`, `.ppt`, `.mmap`, `.msg`, `.vsd`, `.one` — is rejected with `415` **and the conversion that would fix it**, listed in `KNOWN_UNSUPPORTED_EXTENSIONS`. Half-parsing a legacy OLE2 binary would be worse than refusing it: it yields a document of wrong characters that is then embedded, retrieved and cited as though it were the text. A hand upload of one leaves no row; a *synced* one does, so the corpus can show what it is missing.
- **Status:** Implemented
- **Dependencies:** Embedding Generation, Document Models & Migrations, Configuration Management

### Multi-File Upload
`POST /documents/batch-upload` takes many files in one request and reports each one separately: filename, `result`, `succeeded`, `document_id`, `status` and a `reason` where there is one. Files are processed independently, so an unreadable or unsupported file does not stop the ones after it — the response carries per-file outcomes rather than one verdict, and is `200` whatever the individual files did. `result` is one of `indexed`, `unchanged`, `replaced`, `failed`, `unsupported` or `ocr_required`. Reasons name what went wrong and never carry a traceback. Bounded by `MAX_BATCH_UPLOAD_FILES`; a larger batch is `413`, because ingestion is synchronous and holds the request open for the whole batch. Both upload endpoints call one ingestion service — there is no second pipeline.
- **Status:** Implemented
- **Dependencies:** Document Upload & Ingestion

### Document Identity & Idempotent Ingestion
Re-offering a file the knowledge base already holds does not duplicate it. Identity is `content_hash` — the sha256 of the uploaded bytes — optionally qualified by `source_uri`, a stable identifier for where the file came from. Filename is never an identity: two unrelated files are routinely both called `proposal.pdf`, and they stay separate. Same bytes already indexed is `unchanged`: nothing is re-parsed and nothing is re-embedded. Same `source_uri` with different bytes is `replaced`: the document is re-indexed in place, its old chunks deleted and new ones stored, so the previous text stops being retrievable. Without a `source_uri`, edited content is a new document — a plain upload carries nothing tying it to the earlier one. `content_hash` is nullable, and documents ingested before it existed carry `NULL`, which never compares equal to a digest and so is never wrongly deduplicated. `source_uri` and `source_version` are not settable through the API today; they exist for the synchronisation phase to populate.
- **Status:** Implemented
- **Dependencies:** Document Upload & Ingestion

### Document Lifecycle
`pending → processing → indexed | failed | unsupported | ocr_required`. A document is `indexed` only once every chunk carries a vector, so a document retrieval cannot see is never reported as searchable. `failed` records a document this system tried and could not finish — the reason is in `error_message`, and a retry may succeed. `unsupported` is separate because it will not succeed until a parser for that format exists; it is persisted only for ingestion carrying a `source_uri`, so a sync can stop re-offering a file it cannot read, while a hand upload of the same file is still simply rejected. `ocr_required` is a third settled state and is deliberately none of the others: the format is supported, the bytes were read, the parser ran, and the document has no text layer — it is a scan. Retrying cannot help and no new parser would change it; what it needs is optical character recognition, which is not implemented. It carries the tag `OCR_REQUIRED / NO_TEXT_LAYER` in `error_message`, the sync counts it apart from `failed` so a corpus of scans does not make a healthy run look broken, and the delta token still advances over it because the file was genuinely dealt with. Retrieval requires `indexed`, so `failed`, `unsupported` and `ocr_required` documents are unreachable from `/search` and `/chat` whatever chunks they may still hold.
- **Status:** Implemented
- **Dependencies:** Document Models & Migrations

### Embedding Generation
Chunk text is embedded through the configured provider and stored in `document_chunks.embedding`. Requests are batched, vectors are matched to chunks by the provider's index, and a width that disagrees with the column is rejected before storage. The embedding provider can be configured independently of the completion provider.
- **Status:** Implemented
- **Dependencies:** LLM Provider Abstraction, Document Models & Migrations

### Embedding Backfill
Embeds chunks that have no vector — documents ingested before embeddings existed, and documents whose embedding step failed at upload. Idempotent and resumable, committed per document. `python -m scripts.backfill_embeddings`.
- **Status:** Implemented
- **Dependencies:** Embedding Generation

### Embedding Provider Verification
A one-shot live call reporting the configured provider, model and returned width, so a misconfiguration is caught before any document is ingested. `python -m scripts.verify_embedding_provider`.
- **Status:** Implemented
- **Dependencies:** Embedding Generation

### Semantic Retrieval
Top-k cosine similarity search over stored chunk vectors, run in the database against the HNSW index. Configurable `top_k` and similarity floor, scoped by user, restricted to `indexed` documents, with duplicate content collapsed on a whitespace- and case-insensitive fingerprint, so the same passage under two filenames does not take two of the slots a caller asked for. Results carry document, filename, page and heading, so a citation can be resolved. Reached over HTTP through `POST /chat` and, without the model, through `POST /search`.
- **Status:** Implemented
- **Dependencies:** Embedding Generation, Document Models & Migrations

### Dialect-Aware Vector Distance
A `cosine_distance` construct compiling to pgvector's `<=>` on Postgres and to a registered function on SQLite, so the retrieval query under test is the query that ships.
- **Status:** Implemented
- **Dependencies:** Document Models & Migrations

### RAG Chat with Citations
`POST /chat` answers a question using only the caller's indexed documents. Retrieval supplies the context, which is assembled into delimited `[SOURCE n]` blocks naming the document, section and page of each passage; the registered `rag_answer` prompt constrains the model to those passages and instructs it to cite them by identifier. The response carries each passage as `document`, `section`, `page` and `similarity`. Context is bounded by `CHAT_CONTEXT_MAX_CHARS` — passages dropped for budget are not reported as sources, because the model never saw them. Stateless: no conversation history is kept or consulted.

Not every message is a document question. Before retrieval, a deterministic classifier (`chat/intent.py`) recognises greetings ("Hi", "Good morning", "Thanks") and questions to the twin about itself ("Introduce yourself", "Who are you?", "What are your priorities?"). These skip retrieval and are answered from the selected user's profile and memories through the `twin_conversation` prompt, with no sources; a question about the twin when no profile or memory exists gets a fixed reply rather than an invented one. Anything else — including a greeting followed by a real question — goes through retrieval, and a document question nothing matches still gets the fixed no-match reply without a model call. The response's `answer_basis` (`documents`, `no_match`, `conversation`, `profile`) says which happened, and the UI shows "Nothing in the knowledge base matched" only for `no_match`. Provider SDK errors raised by `LLMService` are logged by class and status (never the provider's message) and returned as 502 rather than escaping as an unhandled 500.
- **Status:** Implemented
- **Dependencies:** Semantic Retrieval, Prompt Registry System, LLM Provider Abstraction

### Retrieval-Only Search
`POST /search` runs the retrieval half of the pipeline and stops: query embedding, vector search, deduplication and the similarity floor, returning ranked passages with `document`, `section`, `page`, `similarity` and the passage text. No prompt is rendered and no completion is requested, so relevance can be judged — and `RETRIEVAL_SIMILARITY_THRESHOLD` tuned — without paying for an answer or being persuaded by one. Same 1–50 `top_k` window as chat; nothing found is `200` with an empty result set.
- **Status:** Implemented
- **Dependencies:** Semantic Retrieval

### Knowledge Base Selection & Ingestion
A two-step, reproducible workflow for turning a large mixed corpus into the knowledge base. `python -m scripts.build_kb_manifest --corpus ...` walks the corpus read-only and writes `knowledge_base_manifest.json`, recording every file as included, excluded or needing review, with its reason, category, brand, derived year, and any `duplicate_of` or `superseded_by` relation. Years come from the filename or folder, never from a modification time, which OneDrive rewrote across this corpus. The policy is inclusive and has no per-category limits: a file is dropped only for being unreadable in the current parser, sensitive, a template or form, another organisation's material, a byte-identical duplicate, or a superseded issue of a selected document. The manifest also reports the unsupported formats it left behind, by count, size and largest candidates, so the gap between "ingested" and "complete" is visible. `python -m scripts.ingest_kb_manifest` sends the selected files through the existing upload endpoint, skips what is already indexed so a re-run resumes, continues past individual failures, verifies the database, and writes `knowledge_base_ingestion_results.json`. The source corpus is never modified; exclusion happens at selection time.
- **Status:** Implemented
- **Dependencies:** Document Upload & Ingestion

### Multi-User Digital Twin
Every Digital Twin has an owner. A `users` table holds name, email and role; `POST /users` and `GET /users` create and list them, and the `X-User-ID` request header names which one a call is acting as. Missing is `401`, malformed is `422`, and an id with no user is `404` — the header never creates a user. Profile, memory and the persona half of chat are scoped to that user and every query filters on it. The company knowledge base is deliberately *not* scoped this way: `documents.user_id` and `document_chunks.user_id` remain the shared `MVP_USER_ID`, because the corpus is company-wide and only the twin is private. This is identity, not authentication — the header is trusted as sent.
- **Status:** Implemented
- **Dependencies:** Digital Twin Profile & Memory, Database Connectivity Layer

### Digital Twin Profile & Memory
Who the Shadow answers for, and what it durably knows about them. One profile per user — name, role, organization, responsibilities, expertise, priorities, decision preferences, current focus and communication style — through `GET /profile` and `PUT /profile`, where a partial write updates the fields supplied and leaves the rest. Memories are typed (`fact`, `preference`, `decision`, `commitment`, `context`), carry an importance from 1 to 5, and can be retired rather than deleted: `GET /memory`, `POST /memory`, `PATCH /memory/{id}`, `DELETE /memory/{id}`, filterable by type and active flag. Every memory is written explicitly through the API; nothing is extracted from chat or from ingested documents. A `name` or `role` supplied to `PUT /profile` is also written onto that user's own `users` row in the same transaction: those two fields exist in both tables, and the user record is what every label in the product displays, so a profile that renamed only itself looked from the browser exactly like a save that had not happened. A write carrying neither field leaves the user record untouched — the endpoint that renames a person is their own profile, and there is still no API for editing anybody's identity.
- **Status:** Implemented
- **Dependencies:** Database Connectivity Layer

### Persona-Aware RAG
`POST /chat` loads the profile and the most important active, unexpired memories and puts them above the retrieved passages, under `[DIGITAL TWIN PROFILE]` and `[MEMORY]` headings, with the passages under `[KNOWLEDGE SOURCES]`. The prompt tells the model to use them for tone, emphasis and priorities but never as evidence: only the numbered passages can be cited as `[SOURCE n]`, and `sources` in the response is still built from retrieved chunks alone. The persona block is bounded by `PERSONA_CONTEXT_MAX_CHARS` and subtracted from `CHAT_CONTEXT_MAX_CHARS` rather than added to it, so a Digital Twin cannot grow the prompt past the bound already in place. With no profile and no memories the assembled context is byte-for-byte what it was before the feature existed. The request and response contracts are unchanged.
- **Status:** Implemented
- **Dependencies:** RAG Chat with Citations, Digital Twin Profile & Memory

### Email Agent
Drafting, revising, triage and follow-up recommendations, written as the caller's Digital Twin and grounded in the shared knowledge base when asked. Six services under `app/services/features/email/` plus a provider boundary under `app/services/email/provider/`.

**Generation** is one operation-based endpoint, `POST /email/compose`, taking `generate`, `reply`, `rewrite`, `improve`, `shorten`, `expand`, `change_tone`, `professional`, `concise` or `subject`. Each runs a registered prompt through the Analysis Engine and returns validated `{subject, body}` — so a model that answers with prose produces a `502` rather than an email body containing an apology. The Digital Twin of the user named by `X-User-ID` supplies the persona block through the existing `persona_service`; two people asking for the same email from the same corpus get different prompts and nobody else's twin is ever loaded. With `use_knowledge_base`, the existing `retrieval_service` and `context_service` supply `[SOURCE n]` passages; when retrieval finds nothing the prompt says so explicitly and forbids any company claim, `knowledge_used` is `false`, and `sources` is built from retrieval rather than from the model's output.

**Templates** are user-owned, unique per owner, with `{{ placeholder }}` substitution performed textually on the server — no model call, and an unfilled placeholder stays visible rather than becoming a blank. `GET/POST /email/templates`, `GET/PATCH/DELETE /email/templates/{id}`, `POST /email/templates/{id}/fill`.

**Drafts** hold recipients, subject, body, attachments and provider identifiers. `GET/POST /email/drafts`, `GET/PATCH/DELETE /email/drafts/{id}`, `POST /email/drafts/{id}/attachments`, `DELETE /email/drafts/{id}/attachments/{attachment_id}`. Attachment bytes live in the row, bounded by `EMAIL_MAX_ATTACHMENT_BYTES` and `EMAIL_MAX_ATTACHMENTS_PER_DRAFT`; they are never returned in a JSON response.

**Human approval is structural.** `POST /email/drafts/{id}/approve` records `approved_at`; `POST /email/drafts/{id}/send` refuses anything not in `approved` state, whether or not a mailbox is connected. **Any edit to a draft — body, subject, recipients, attaching or detaching a file — withdraws the approval**, so an approved draft is always the text that was approved rather than whatever the row holds at send time. A sent draft is immutable. A provider failure records `failed` with the reason and withdraws approval, so the next attempt is a new decision.

**Triage** classifies one message as `urgent`, `needs_reply`, `fyi`, `follow_up` or `low_priority` with a separate priority, summary, suggested action, action items and a follow-up recommendation, judged against the caller's twin. `POST /email/triage` takes either a `message_id` from the connected mailbox or a `message` the caller supplies — the second is what makes triage usable before any mailbox exists. Assessments are stored keyed by provider and message id and updated in place on re-assessment, because assessing costs a model call. Only the assessment plus subject, sender and timestamp are stored; **message bodies are not**. `POST /email/threads/summarize` summarises a conversation and stores nothing, because a stored summary of a growing thread is wrong as soon as somebody replies.

**Follow-ups** are recommendations. `GET /email/follow-ups` lists them soonest-due first with undated ones last, and `POST /email/follow-ups/{id}/handled` is pressed by a person. A due date appears only where the message itself gave one — the prompt forbids choosing a deadline.

Everything is user-scoped: templates, drafts and assessments are private, and another user's is a `404` rather than a `403`. No request body carries a `user_id`.
- **Status:** Implemented — **no live mailbox has been connected**
- **Dependencies:** Multi-User Digital Twin, Persona-Aware RAG, Analysis Engine, Prompt Registry System, Microsoft Graph Client

### Activity Reports
Reports answer "what happened"; Tasks answers "what do I need to do". Keeping those apart is what the Reports section is for. Two documents — a **Weekly Activity Report** and a **Monthly Activity Report** — in eight sections: executive summary, major workstreams, important conversations, decisions and outcomes, requires follow-up, completed work, risks and issues, and the bottom line. A stored report downloads as `.docx` from `GET /reports/document`, rendered from the snapshot rather than rebuilt, so the file and the screen agree today and next month.

**Nothing in a report is invented and no model writes any of it.** Every line is assembled from a count or a row: correspondents from the digest, completed and outstanding work from tasks, risks from what is more than two days late. "Major workstreams" is explicitly a grouping by correspondent rather than an inferred project list — inferring a project from a set of subject lines is a guess, and a guess in a report somebody signs is their problem. Sections with nothing in them are omitted rather than printed over the word "None". A period with no mailbox still produces a report: the task half is real, and the email half says it was unavailable.

The interactive weekly work view — meetings needing an answer, escalations — is still reachable as the last tab, because those are actions on live work and a snapshot has nothing to act on.

### Report persistence and history
Three report types, one envelope, and history that does not move. `GET /reports?report_type=&period=&refresh=`, `GET /reports/history?report_type=`, `POST /reports/digests/run`. The existing `GET /reports/weekly` is unchanged and still serves the live work report.

**A period actually selects that period.** The work report is evaluated as of `min(period_end, now)` — the instant the week ended, or now if it has not. A task is included only if it existed by then (`created_at`), and whether it was outstanding is read from `completed_at` rather than from `status`, which is only ever current. So last week shows the work that was open last week, a task finished since reads as it read then, and a completion counts in the period it happened in. The selector offers the running period, the one just finished, and any period this user has a stored report for — not a dozen guaranteed-empty months.

**Periods are calendar arithmetic, not "the last seven days".** `app/services/features/reports/period.py` is pure: ISO weeks beginning Monday and calendar months, in UTC, half-open (`start <= t < end`) so a message at a boundary belongs to exactly one period. A period's `key` — `2026-08-31` for a week, `2026-08` for a month — round-trips, and a week key that is not a Monday is a `400` rather than a silent swap for the current week.

**A closed period is written once and read back forever.** `generated_report` stores the rendered report as JSON, unique per `(user_id, report_type, period_start)`. A report generated while its period is still running is marked `is_provisional` and is replaced on the next request; once the period closes the next generation writes the final row and nothing overwrites it after that. That is what makes "what did the last week of August say" answerable — regenerating a work report a month later would describe a month-later set of tasks.

**The two email digests count real mailbox activity** for the period: received and sent, triage's categories and priorities where triage ran, top correspondents, what is waiting on a reply, and what was flagged urgent. Deterministic — no model call, no prompt. A message nobody has triaged is reported as `untriaged` and is never given a category; a message the provider returned without a timestamp is counted in no period at all.

**"Could not be produced" is a different row from "nothing happened".** With no mailbox connected, the digest is recorded with `status = unavailable`, an empty `content` and the reason in words — never as an empty digest, which would assert that a mailbox was read and held nothing. The provider boundary offers no date range, so a bounded page can cut a window short; when the oldest message fetched still falls inside the period the digest reports `truncated` rather than presenting a partial count as a total.

**Scheduling reuses `SyncScheduler`** — no workflow engine, no second piece of infrastructure. `REPORT_DIGEST_SCHEDULE_ENABLED` (off by default) runs a tick that asks whether each user's last *completed* week and month are recorded and records them if not. Idempotent by construction: a period already snapshotted costs one indexed lookup per user per type and no mailbox call.

Stored reports are owned by their user, cascade on deletion, and are listed in the deletion preview. History filters on `user_id` unconditionally; there is no call shape that returns another person's report.
- **Status:** Implemented — the digests have **never been generated against a live mailbox**, because no mailbox has been connected
- **Dependencies:** Email Agent, Email Provider Abstraction, Multi-User Digital Twin, Scheduled Synchronization

### Tasks
One page answering one question: what do I need to do. `GET/POST /tasks`, `PATCH/DELETE /tasks/{id}`, `POST /tasks/{id}/complete`.

**Four colours, decided on the server.** `services/features/tasks/signal.py` maps completion and the deadline to `todo`, `attention`, `urgent` or `done`; the frontend renders grey, amber, red and green and subtracts no dates. Lateness is counted in **whole days**, which is what makes "exactly two days is amber, more than two is red" a band rather than a knife-edge: a task 2 days and 6 hours late reads as "2 days overdue" and is amber for the whole of that day. A task with no deadline is grey forever — nothing was promised, and inventing a date to have something to colour is the one thing this does not do. `completed` and `cancelled` share the green signal because neither needs attention; the label distinguishes them from `status`.

**A task arrives one of two ways and both produce the same row.** A person types it in the New Task form — title, optional summary, optional deadline, priority, and nothing else, because every other column exists to carry what a triaged email knew — or a triaged email becomes one through `POST /tasks/from-follow-up/{assessment_id}`, which returns the task so the UI can show it. Pressing that twice returns the same task: `source_key` is the message's identity and is unique per user. The summary is triage's own; no second model call produces a second opinion.

The bulk `POST /tasks/from-follow-ups` remains for sweeping every open follow-up at once.
- **Status:** Implemented
- **Dependencies:** Email Agent, Multi-User Digital Twin

### Task Lifecycle
`todo → in_progress → completed`, with `blocked` and `cancelled` off to the side. Moves go through `PATCH /tasks/{id}` (or `POST /tasks/{id}/complete`, which is the same operation named for the button that presses it), and the allowed moves are declared once in `task_service._ALLOWED_TRANSITIONS` rather than checked at each call site.

Completion is nearly terminal, and the asymmetry is the whole rule. Marking a finished task **to do** is an explicit correction — the work came back — and clears `completed_at`, because a reopened task still claiming a completion time is a lie. **Starting** a completed task is refused with a `409`, because that is a lifecycle step assuming the work was outstanding, and allowing it would let "done" drift into "in progress" through a misclick. A refused move leaves the row exactly as it was. Same-state moves are no-ops, so pressing a button twice is a person being unsure rather than an error.

Started work is still outstanding work: it stays overdue, stays escalated, and is not an achievement. Completion is what moves it out of the open sections and into `completed`.
- **Status:** Implemented
- **Dependencies:** Multi-User Digital Twin

### Administrator Bootstrap
`is_admin` guards exactly one operation — deleting another person's Digital Twin — and no endpoint grants it, because one that promoted the caller would make the check decorative. That left a deadlock: a deployment with users and no administrator could never gain one. On startup, if nobody is an administrator, exactly one user is promoted and the promotion is logged as a warning: the user named by `BOOTSTRAP_ADMIN_EMAIL`, or the earliest-created user — the CEO, the first Digital Twin — when that is unset. Nobody is ever demoted, no second person is ever promoted, and no user is ever created; a fresh install has none, and the first user made through the API becomes the administrator on the next start. `role` is never read as permission.
- **Status:** Implemented
- **Dependencies:** Multi-User Digital Twin

### Email Provider Abstraction
`EmailProvider` in `app/services/email/provider/base.py` is a `Protocol` over provider-neutral dataclasses — `EmailMessage`, `EmailAddress`, `AttachmentRef`, `OutgoingEmail`, `SendReceipt`, `ProviderStatus`. Nothing above it contains a vendor's word, and nothing in the package imports a service, a model or a schema. `registry.get_provider()` returns the configured provider or raises; `registry.status()` describes the connection and never raises, so a UI can render a setup notice rather than an error page.

**There is no null provider, no in-memory provider and no demo provider.** A provider that accepted a send without a mailbox would make every "Sent" badge indistinguishable from a real one. With nothing configured, sending is a `409` and the UI disables the button — that is the entire fallback. Adding Gmail is one entry in `_BUILDERS` plus a module beside `outlook_provider.py`.
- **Status:** Implemented
- **Dependencies:** Configuration Management

### Outlook Provider (Microsoft Graph)
`OutlookEmailProvider` translates Graph's JSON into the neutral types and back: `list_messages`, `get_message`, `get_thread`, `get_attachment`, `create_draft` and `send`, over `/users/{mailbox}/…`. It reuses the existing `GraphClient` — the same token cache, throttling retries and error translation OneDrive sync uses — so there is **no second Graph authentication**. `GraphClient` gained one additive method, `post()`, and a `json` argument on its internal `_send`; nothing existing changed behaviour.

A reply goes through `/messages/{id}/reply` rather than `/sendMail`, so it threads correctly. Graph's `sendMail` returns no message id, and `SendReceipt.message_id` is left `None` rather than filled with a guess. A `403` is translated into an auth error that names the likely cause — mail consent is separate from files.

Configured by `EMAIL_PROVIDER=outlook` and `EMAIL_MAILBOX_ADDRESS`, reusing `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID` and `ONEDRIVE_CLIENT_SECRET`. That application additionally needs the `Mail.Read` and `Mail.Send` application permissions with admin consent.
- **Status:** Implemented and unit-tested against a mock transport — **never executed against a live tenant**. Nothing in this repository establishes that a real mailbox answers as these tests assume.
- **Dependencies:** Email Provider Abstraction, Microsoft Graph Client

### Calendar Import (Microsoft Graph)
Real meetings, read on request and recorded without a judgement attached. `GET /calendar/status` probes `/users/{mailbox}/calendar` once and **never raises** — "the application has no `Calendars.Read` consent" is the answer worth showing, and a screen that has to catch an exception to show it will eventually not. `POST /calendar/import` takes an explicit window and records what Graph returns.

**`calendarView`, not `events`.** `/events` returns a recurring meeting once with a recurrence pattern attached; a report would either count one meeting instead of five or have to expand the pattern itself, which is a calendar engine nobody asked for. `/calendarView` asks Graph to expand it.

**An import never decides attendance.** Every imported row is `scheduled` whatever its date, exactly as a hand-typed one is, so a past meeting nobody has answered for reads `unknown`. Graph does report whether an invitation was accepted; accepting an invitation is not going to the meeting, and `GraphEvent` has nowhere to put the confusion. Re-importing a window updates the fields Graph owns — title, times, location, organiser — and leaves `status`, `attendance_evidence`, `attendance_note` and `attendance_recorded_at` untouched, because erasing an answer somebody gave is worse than a missing meeting.

**Idempotent with no migration.** `(user_id, provider_event_id)` has been unique on `calendar_event` since the model was written, against the day consent arrived; this is that day. A cancelled occurrence is kept and retitled rather than deleted — deleting it would silently remove a meeting somebody may already have planned around. A time in an unexpected zone is dropped rather than assumed UTC: a meeting five and a half hours from where it was looks entirely correct on screen.

Refusals are refusals rather than empty results. No mailbox, or a window that is not a window, is a `503` — an empty result would be indistinguishable from a genuinely free week, and a report built on it would be claiming a calendar was read. Reuses the existing `GraphClient` and `mailbox_config_service`, so there is no second Graph authentication and no mailbox is named anywhere but a user's own row. `Calendars.Read` is a separate consent from `Files.Read.All` and `Mail.Read`, and the 403 message says so, because re-granting either of the others cannot help.
- **Status:** Implemented and unit-tested against a mock transport — **never executed against a live tenant.** Nothing here establishes that a real calendar answers as these tests assume.
- **Dependencies:** Microsoft Graph Client, Email Provider Abstraction, Multi-User Digital Twin

### Document Management API
List documents with status filtering and pagination, retrieve one by id including its failure reason, and delete a document with its chunks. `GET /documents`, `GET /documents/{id}`, `DELETE /documents/{id}`.
- **Status:** Implemented
- **Dependencies:** Document Models & Migrations

### Document Models & Migrations
`documents` and `document_chunks` tables via SQLAlchemy models and an Alembic baseline migration, including the pgvector extension, a nullable `vector(1536)` embedding column, and its HNSW index.
- **Status:** Implemented
- **Dependencies:** Database Connectivity Layer

### Domain Error Mapping
Every document error maps to a specific status code (`404`, `413`, `415`, `422`) with a consistent JSON body. LLM errors map to `502`. Routes and services never construct `HTTPException` directly.
- **Status:** Implemented
- **Dependencies:** None

### Configuration Management
Environment-driven settings via Pydantic Settings, with a working default for every value so the application imports without a populated `.env`.
- **Status:** Implemented
- **Dependencies:** None

### Database Connectivity Layer
SQLAlchemy engine, session factory, and a FastAPI session dependency. SQL echo follows `DEBUG`.
- **Status:** Implemented
- **Dependencies:** None

### LLM Provider Abstraction
Provider-agnostic chat completions and embeddings across OpenAI and OpenRouter, selected by configuration and cached per provider. Clients are constructed lazily on first use, so importing the package requires no credentials.
- **Status:** Implemented — both paths now have callers
- **Dependencies:** None

### Prompt Registry System
`PromptTemplate`, `PromptRegistry` and `PromptBuilder`, with seven built-in prompts: `rag_answer` used by chat; `email_compose`, `email_transform`, `email_subject`, `email_triage` and `email_thread_summary` used by the Email Agent; and `assistant`, which still has no caller.
- **Status:** Implemented
- **Dependencies:** None

### Analysis Engine
Runs a registered prompt, calls the LLM, tolerates markdown-fenced JSON, and validates the result against a caller-supplied Pydantic model.
- **Status:** Implemented — used by every Email Agent operation
- **Dependencies:** Prompt Registry System, LLM Provider Abstraction

### Health & Root Endpoints
`GET /health`, `GET /`.
- **Status:** Implemented

> **The Analysis Engine now has callers.** Every Email Agent operation runs through it: composing and revising return validated `{subject, body}`, and triage returns a validated category, priority and action list. The previously recorded "carried without a caller" exception is closed. Chat still derives its sources from retrieval rather than from validated model output — see the RAG Chat decision in `DECISIONS.md` — so it remains the one path that does not use the engine.

---

## In Progress

None. See [`ROADMAP.md`](ROADMAP.md).

---

## Planned

### Sentence-Level Citations
Attribution of individual claims to specific passages, by having the model cite by index and validating those indices through the Analysis Engine. Today every retrieved passage is returned as a source, including any the model did not use.
- **Status:** Planned
- **Dependencies:** RAG Chat, Analysis Engine

### OneDrive Synchronization
Keeps the knowledge base in step with configured OneDrive folders, over Microsoft Graph. Folders come from `ONEDRIVE_SOURCES` — a JSON array of `{key, label?, drive_id? | user?, path | item_id | share_url, uri?, enabled?}` — so adding one is a configuration change; no folder is named in code. `enabled: false` pauses a source without deleting it: the delta token is stored against the key, so re-enabling resumes rather than re-indexing the folder, and a disabled source is still reported by the status endpoint because "switched off" is information and a vanished row reads as a mistake. Each file is downloaded to a temporary path, passed to the same ingestion service the upload endpoints use, and the staged copy is removed whether ingestion succeeded or not. OneDrive is never mirrored locally.

Identity is the Graph item: `source_uri` is `onedrive:{drive_id}:{item_id}` and `source_version` is the item's cTag, which changes on a content edit but not on a metadata one. So an unchanged file is skipped without being re-parsed or re-embedded, a modified file re-indexes in place and its old chunks stop being retrievable, a renamed or moved file stays one document, and a deleted file takes its document and chunks with it.

The first run is a full enumeration that also establishes a delta token; later runs ask Graph only for what changed. An expired token falls back to a full resynchronisation, which is safe rather than expensive-and-wrong, because unchanged content is skipped. `POST /sync/onedrive` runs one source or all of them and reports per-file results; `GET /sync/onedrive/status` reports stored state without contacting Graph and never returns the delta token. `ONEDRIVE_SYNC_ENABLED` adds a periodic incremental run.

Failure is scoped at three levels: one file's failure is that file's, one source's failure is that source's, and a source that fails before any work is done keeps its state untouched including its delta token. A source is released from `running` on every outcome, so an unforeseen error cannot leave a folder blocked behind a flag nobody set deliberately.

**A failure is transient or permanent, and one bit decides the delta token.** `GraphError.transient` is set by the layer that knows the HTTP status, and the run withholds its token only if something genuinely transient happened — a 5xx, or a 429 that outlived its retries, meaning the bytes were never seen. A *permanent* failure has been dealt with as fully as it ever will be: a legacy format, a corrupt PDF, an item the application is not allowed to open. Withholding the token for those means the folder is re-enumerated on every run, reaches the same conclusion, and never finishes arriving — which is exactly what a real 847-file run did. When a run does end `partial`, the persisted `error_message` names the causes and their counts rather than only the total, because "405 file(s) failed transiently" cannot distinguish an expired URL from a throttled one from a missing consent, and those have three different remedies.

**Pre-authorised download URLs are re-minted, not trusted.** Graph attaches a download URL to every item during enumeration and gives it about an hour. A run that enumerates 847 files and then downloads them is holding URLs from before it started, so anything reached after the first hour answers 403 — a file that is perfectly readable, failing in a way that reads as a permissions problem. `GraphClient.download` takes a `refresh` callable and uses it **once** for 401 and 403; a freshly minted URL that is still refused is a real refusal and is recorded as permanent.

**Work is arranged to avoid downloads rather than to speed them up.** A file over the size ceiling, or in a format the parser does not handle, is decided from the folder listing — no bytes are fetched for a rejection the filename already settled, which removed a third of the downloads from the SunRadia corpus. What remains is downloaded a batch at a time (`ONEDRIVE_DOWNLOAD_BATCH_SIZE`) across a small thread pool (`ONEDRIVE_DOWNLOAD_CONCURRENCY`, default 4, deliberately modest because Graph throttles a first full sync hard). Everything after the download — parsing, chunking, embedding, every write — stays on the calling thread with the session it was given, because `Session` is not thread-safe and sharing one across a pool is a corruption bug that only appears under load. The batch also bounds how long a download URL waits before it is spent.
- **Status:** Implemented

### OneDrive Source Resolution
Turns a configured folder into the `drive_id` and `item_id` Microsoft Graph answers to, by asking Graph. Three ways a folder can be named, three questions: a path in a known drive, an item id in a known drive, or a **sharing link** resolved through `/shares/{token}/driveItem`. The last is the only route for content that arrived as a share, because such a folder's drive id is not written down anywhere. Resolution runs on the sync path as well as ahead of time, so configuring a link is enough for a first run — no pinning step stands in between.

A shortened link (`1drv.ms`) is followed before it is resolved, because `/shares` is told a URL and a short link is a redirect rather than the URL of anything. Following it also reveals the destination host, which is the single fact that decides whether an application-only token can ever reach the content: a `*.sharepoint.com` or `*-my.sharepoint.com` host is in-tenant and covered by `Files.Read.All`, while `onedrive.live.com` is a consumer Microsoft account that belongs to no tenant and that no consent granted to this application can reach. That case is reported as `outside_tenant` **without asking Graph**, because the 403 Graph would return is indistinguishable from a missing permission and sends people granting permissions that cannot help. `access_denied` (in-tenant, not consented — an administrator can fix it) and `not_found` (a wrong path — ours to fix) are separate verdicts for the same reason.

A shortcut to a shared folder — what "Add shortcut to My files" leaves behind — is followed to its target through the item's `remoteItem`. The stub has no children and no delta of its own, so a sync pointed at the stub's id finds an empty folder and reports success: a silent, complete failure.

Turns a configured folder path into the `drive_id` and `item_id` Microsoft Graph answers to, by asking Graph. **No id is ever derived from a URL** — a sharing link contains something that looks like an id, and decoding one is the usual way an integration ends up pointed confidently at the wrong folder. `source_resolver.resolve_sources` resolves each configured source independently, so one misspelled path reports an error while the rest resolve, and `to_configuration` renders the successes as an `ONEDRIVE_SOURCES` line with ids filled in. `python -m scripts.discover_onedrive_sources --resolve` is the command-line face of it, and exits non-zero when anything failed to resolve, so it doubles as a deployment check. Not on the synchronisation path: the sync service resolves lazily on first contact and stores what it got. Resolving ahead of time moves a bad path from a 3am failure to a configuration-time one.
**A folder can be named by the person who owns the drive.** A source giving `"user": "someone@example.com"` has that person's OneDrive for Business resolved through `/users/{upn}/drive`, so no drive id is pasted into configuration. Such a source deliberately never falls back to `ONEDRIVE_DRIVE_ID`: addressing the wrong drive silently is worse than failing to resolve.

**A web address is read, and only read.** `python -m scripts.discover_onedrive_sources --url "<url>"` turns the URL from a browser's address bar into the entry to write, offline and with no credentials. It strips the `Documents` library prefix, because a OneDrive for Business drive root **is** `Documents` — a path of `Documents/Knowledge Base` 404s on a folder that is plainly there — and it reads the folder out of the `id` query parameter rather than the path, where `_layouts/15/onedrive.aspx` sits. It reports the personal site name and refuses to turn it into an address: the site name replaces every `@` and `.` with `_`, so `a_b_c_com` could be `a.b@c.com` or `a@b.c.com`, and a guess would silently address a different person's drive.
- **Status:** Implemented
- **Dependencies:** OneDrive Synchronization, Microsoft Graph Client

### Knowledge Base Status
What the Knowledge Base screen shows, assembled from two endpoints that each own their facts. `GET /sync/onedrive/status` owns per-source state: every configured source appears — including ones that have never run, ones switched off, and ones whose configuration was removed but whose documents are still indexed — with its status, last attempted and last successful run, and the per-outcome counts of its last run. `GET /documents/stats` owns corpus totals: documents by ingestion status, total chunks and how many carry an embedding, counted in the database. The counts were previously derived in the browser from whichever page of documents happened to be loaded, which was a sample presented as a total. No aggregate `/kb/status` endpoint exists, because composing two endpoints that already own their halves is cheaper than a third that restates both.
- **Status:** Implemented
- **Dependencies:** Document Identity & Idempotent Ingestion, Microsoft Graph Client

### Microsoft Graph Client
Authentication and transport for OneDrive, isolated from everything that knows what a document is. Throttling is treated as part of the protocol rather than as failure: `429`, `503` and `504` are retried, honouring Graph's `Retry-After` where it is given and backing off exponentially where it is not, capped so one bad header cannot stall a run and bounded so a genuinely dead endpoint still surfaces. This matters most on a first synchronisation of a large corpus, which is exactly the traffic shape that provokes throttling — without it a throttled file would be recorded as a transient failure, the delta token would be held back, and the run would never complete. The OAuth 2.0 client-credentials flow — the application acts as itself, because a background job has nobody present to complete a consent prompt — with the token cached until shortly before it expires. Handles `@odata.nextLink` paging, delta collections, and pre-authorised download URLs, and translates Graph's failures into domain errors: 403 becomes an auth error naming the likely missing consent, and 410 or a `resyncRequired` code becomes a delta-expiry the sync service knows how to recover from. Query strings are stripped from error messages, because that is where Graph puts its tokens.

Two bounds keep one slow file from deciding a run's length. Retries stop when the **total** time spent waiting passes `_MAX_TOTAL_RETRY_SECONDS`, not only when the attempt count runs out: five attempts each honouring a 60-second `Retry-After` is five minutes inside one file, and several hundred of those is a sync measured in days. And `download()` classifies its own failures rather than reporting them all alike — permanent for 400/404/410 and for a refusal that survives a re-minted URL, transient for an exhausted 429 or a 5xx.
- **Status:** Implemented
- **Dependencies:** Configuration Management

### Scheduled Synchronization
An asyncio task started from the application lifespan when `ONEDRIVE_SYNC_ENABLED` is set, running an incremental sync every `ONEDRIVE_SYNC_INTERVAL_SECONDS`. Off by default. A failing run is logged and the loop continues, because the usual cause is the far end being briefly unavailable and the sync state is built to make a retry safe. Kept separate from the sync service, so a sync can still be run by hand and tested with no scheduler present.
- **Status:** Implemented
- **Dependencies:** OneDrive Synchronization

### Frontend
A chat workspace in React, Tailwind and Vite, under `frontend/`. The conversation is the primary surface; documents are attached from the composer rather than from a separate screen, so uploading is part of asking rather than a detour. Multi-file selection goes through `POST /documents/batch-upload`, and each file's outcome is shown distinctly — `indexed`, `unchanged`, `replaced`, `unsupported`, `ocr_required`, `failed` — because the ingestion layer distinguishes them and collapsing them would hide idempotency working. Retrieved passages render as source cards beneath the answer, separate from it; the persona note says profile and memory shape the wording and are never cited, matching what the backend actually builds `sources` from. A knowledge-base panel lists, filters and deletes documents; a Digital Twin panel reads the selected user's profile and memories. The user selector sends `X-User-ID` and is labelled as identity rather than sign-in.

All API access goes through one client layer whose types mirror `backend/app/schemas/`. Development proxies `/api` to the backend, since no CORS middleware exists; production expects the built files to be served same-origin.
- **Status:** Implemented
- **Dependencies:** RAG Chat with Citations, Multi-File Upload, Multi-User Digital Twin

### Authentication
Verifying that a caller is who `X-User-ID` says they are. Multi-user data isolation already exists for the Digital Twin, so this is a change to where the identity comes from — a session or token replacing a trusted header — not a migration or a backfill.
- **Status:** Planned
- **Dependencies:** Multi-User Digital Twin

### Background Ingestion
Move ingestion off the request thread once documents are large enough for synchronous processing to be a problem.
- **Status:** Planned
- **Dependencies:** Document Upload & Ingestion

---

## Explicitly out of scope for the MVP

Autonomous agents, CRM, LangGraph and n8n are deferred entirely. **Email and the calendar are no longer deferred** — see the Email Agent and Calendar Import above — but everything autonomous about both still is: nothing schedules a send or an import, nothing contacts anybody without a person approving it, nothing acts on a meeting, and no model can reach a provider.

Carried over from the reference repository's design but deliberately not built here: the AI Orchestrator, conversation/user/task memory, the tool architecture (email, calendar, research, search), document generation, and multi-agent workflows.

### Activity reports — evidence-based, weekly / monthly / month-to-date

`GET /reports/activity?start=&end=` produces a detailed management activity report for any window. Omitting both dates reports the current month so far. The report is built from correspondence, not from counts: a six-stage pipeline under `app/services/features/reports/` collects whole messages with their bodies and conversation ids (`correspondence.py`), separates automated and bulk mail from substantive correspondence deterministically (`classification.py`), rebuilds conversations and derives who spoke last and whether the owner replied (`threads.py`), asks a model what those conversations were about and what they mean (`analysis.py`), renders the fourteen-section document (`composition.py`) and checks it against the evidence volume behind it (`quality.py`). `pipeline.py` is the single entry point. Both directions of the mailbox are read through `EmailProvider.list_messages` — the inbox, and then the sent folder, each bounded by `since`/`until` so a report reads the period it is about rather than the newest page of a mailbox — because a report that cannot see the owner's replies cannot say what they did; a listing that does not reach back past the start of the period is reported as truncated in the header rather than presented as the whole window. Whether a model is used is answered by `llm_client.llm_configured()`, which reads the credential belonging to the provider `LLM_PROVIDER` names.

**Any period longer than a week is analysed week by week and then read across.** `period.py` gained `PeriodKind.RANGE`, `range_between`, `to_date` and `split_into_weeks`, so "September so far" on the 13th resolves to 1–13 September, splits into 1–7 and 8–13, and produces weekly sections plus a cross-week synthesis identifying what carried forward. The split is arithmetic from the period's own start, so it works for any month including a leap February; nothing is hardcoded.

**Generated claims are gated on evidence, not on instruction.** Every finding, workstream, decision, risk and follow-up names the message ids it rests on, and any claim citing an id that is not in the period is discarded before composition. A thread the model invents has no id and cannot reach the page.

**The report never depends on a model being available.** With no LLM configured, or when a provider fails, the pipeline returns a structural analysis of the same real threads — who corresponded, when, which side replied last — and the header says `structural analysis only` rather than presenting it as an analysis. Measured on the August corpus, that fallback is 1,868 words over 188 messages.

**On screen, a failure is never dressed as a report.** The Reports panel shows the stored email digest in place of the detailed report for exactly one reason — no mailbox connected, which the endpoint answers with 409 — and says it is doing so. Any other failure is shown as the error it is, with its status, and nothing is rendered beneath it (`lib/reportFault.ts`).

Tasks are supplementary: they are passed to the analysis stage as context with explicit wording that a period with no tasks is not a period with no work. Calendar unavailability is recorded as a limitation in the header and does not shorten the rest of the report.

**A report is generated once and then read.** `GET /reports/activity` looks in `activity_report` for this user, this exact window and this `report_version` before it does anything else. A window that has closed cannot change, so the stored document is returned — no mailbox, no model, a database read. A window still running is served from storage while it is fresh (`ACTIVITY_REPORT_FRESH_SECONDS`, 30 minutes); after that the mailbox is re-read and the correspondence fingerprinted, and an unchanged fingerprint means the stored report still stands, so the row's `checked_at` moves and no model is called. `source` in the response says which of the three happened — `stored`, `revalidated` or `generated` — and `generated_at`, `checked_at` and `data_through` are three different instants, all shown.

**`POST /reports/activity` is the only thing that rebuilds one.** It is what the Regenerate button sends; opening a tab, switching back to a period and reloading the page all use `GET` and reuse storage.

**One generation at a time.** The row exists before the work does, in status `generating`, and the unique constraint on `(user_id, period_start, period_end, report_version)` is what makes that claim atomic. A second request for the same window gets `202` with `state: "generating"` and the claim's age rather than a second pipeline; a claim older than `ACTIVITY_REPORT_LOCK_SECONDS` is treated as abandoned and taken over. A generation that raises records `failed` with its reason and never a row that reads as a finished report.

**Raising `REPORT_VERSION` retires every stored report.** The version is part of a row's identity rather than a field to check, so a prompt or composition change simply stops matching: the next request generates under the new version beside the old row, and nothing is deleted or served under the wrong machinery.

**One request is bounded, and says what it spent.** Every model call is bounded by `LLM_TIMEOUT_SECONDS` and `LLM_MAX_RETRIES`, and all the calls behind one report share `REPORT_ANALYSIS_BUDGET_SECONDS`. When that budget is spent the remaining conversations are described structurally and the header says so. The per-conversation analysis runs once per period and is reused by each week inside it, so a month costs one pass over its threads rather than two. The pipeline logs its period, message count, per-batch outcome and elapsed time — counts and identifiers only, never a subject, a body or an address — and `scripts/diagnose_activity_report.py` reruns one report against the live configuration and prints every model call with its exception.

Quality control is evidence-volume-aware rather than a fixed word count. A quiet period legitimately produces a short report; a period with a hundred substantive conversations producing five bullets is a failure, and triggers one fuller re-composition pass.
