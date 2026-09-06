# AI Shadow MVP — Known Issues

Current gaps, limitations and accepted tradeoffs. For what exists, see [`FEATURES.md`](FEATURES.md).

---

## Limitations

### No authentication
There is no auth layer. A caller states who they are with the `X-User-ID` header and is believed.
- **Impact:** any caller can act as any user. Do not store production data in it.
- **Mitigation in place:** the Digital Twin is genuinely user-scoped — `users` exists, profile and memory are UUID-keyed to it, and every query filters on that key — so adding auth is a change to where the identity comes from, not a migration or a backfill. The knowledge base is shared by design and is not affected.
- **Priority:** High — before real user data.

### The corpus has not yet been ingested through Graph
Credentials are in place and the owner reports that token acquisition,
`Files.Read.All` consent and drive visibility all work from their machine. That
verification is theirs; **no synchronisation has been run from this
repository's own code against a live tenant**, and every Graph behaviour here
is verified against a mock transport only. The five configured folders have
therefore never been resolved to real ids, and nothing has been downloaded.
- **Impact:** discovery counts, real throttling behaviour, folder resolution against the actual drive, and the true supported/unsupported split across the corpus are all unmeasured. The corpus is not in the knowledge base.
- **Mitigation in place:** `python -m scripts.discover_onedrive_sources --resolve` resolves every configured folder read-only and reports what it could not find, so the first contact with Graph enumerates rather than ingests, and a wrong path is discovered before any embedding is paid for.
- **Priority:** High — this is the last unverified link in the chain.

### The knowledge-base folders were shared as `1drv.ms` links
The five folders were supplied as shortened Microsoft sharing links rather than
as paths in a known drive. A short link hides which service holds the content,
and that is the fact deciding whether an application-only token can reach it at
all — so it has to be followed before anything can be concluded.
- **Impact:** until each link's destination host is known, a `403` from Graph is ambiguous between "the application lacks consent for this tenant's content" and "the content is not in this tenant at all". The second cannot be fixed by any permission grant, and mistaking it for the first is an open-ended loop of granting permissions.
- **Mitigation in place:** `python -m scripts.discover_onedrive_sources --diagnose` follows each link, classifies the destination, and reports a separate verdict per source — `resolved`, `not_found`, `access_denied`, `outside_tenant` or `unreachable` — with the remedy that matches. `outside_tenant` is decided from the host without asking Graph, precisely so its `403` cannot be misread.
- **Priority:** High — it gates the corpus.

### `Files.Read.All` was over-specified as needing `Sites.Read.All`
Earlier configuration notes and the Graph client's own `403` message told
readers the application "may lack Files.Read.All or Sites.Read.All consent".
As an *application* permission, `Files.Read.All` is defined as read access to
files in all site collections — SharePoint document libraries included — so
`Sites.Read.All` was never required for anything this application does. It
governs the `/sites` discovery endpoints, which are not called.
- **Impact:** a `403` pointed at a permission that could not have caused it, which is how a permissions investigation becomes a week long.
- **Mitigation in place:** corrected in `app/services/graph/client.py`, `.env.example` and the README. The message now names `Files.Read.All` alone and says the alternative is content outside the tenant.
- **Priority:** Resolved.

### Enum columns carry no database-level CHECK constraint
Every enum column in the schema is created with `sa.Enum(..., native_enum=False)`,
whose `create_constraint` has defaulted to `False` since SQLAlchemy 1.4 — so the
`varchar + CHECK` the surrounding code comments describe was never emitted, on
any table. Verified against a real PostgreSQL 16 database: `pg_constraint` holds
no check rows for `task`.
- **Impact:** a value outside the enum can be written by anything that bypasses the ORM — a manual `UPDATE`, a migration, a future service using Core. Application writes are still validated by Pydantic at the boundary and by SQLAlchemy's enum on the way in, so nothing in the running system can produce one.
- **Mitigation in place:** none beyond the application layer. Migration `0011` documents the finding rather than quietly emitting no-op `ALTER COLUMN` statements that would imply a guarantee the database does not give.
- **Priority:** Low — closing it means adding constraints to every enum column across seven tables, which is its own change with its own migration.

### Source status is per-run, not cumulative
`onedrive_sync_state` stores the counts of the **last** run per source, so
`last_indexed` after a quiet incremental run is a small number even when the
folder holds hundreds of documents. Corpus size comes from
`GET /documents/stats` instead.
- **Impact:** a reader could mistake "3 files added last night" for "3 files in this folder". The Knowledge Base screen labels them "Examined/Added/Updated" for the last run to avoid it, but the underlying table genuinely holds no history.
- **Mitigation in place:** totals are read from the corpus, not summed from source rows.
- **Priority:** Low — a per-source document count would need either a run-history table or a `source_key` column on `documents`, and neither has earned its place yet.

### No mailbox has ever been connected
The Outlook provider is implemented and unit-tested against a mock transport.
No SunRadia mailbox has been reached, and the `Mail.Read` / `Mail.Send`
application permissions have not been granted to the Entra application.
- **Impact:** listing an inbox and sending do not work. Every Graph mail request shape in this repository is asserted against recorded payloads, not against a live tenant, so the response mapping could still be wrong in ways only a real mailbox will show.
- **Mitigation in place:** the unconnected state is a supported, tested state rather than a failure — composing, revising, templates, drafts and triage of a supplied message all work without a mailbox. `GET /email/provider/status` reports exactly what is missing and distinguishes "not configured" from "configured but refused", because those need different fixes. There is no simulated provider, so nothing can appear to work when it does not.
- **Priority:** High — it is the only thing between the Email Agent and real use.

### Mail permissions are separate from file permissions
The Email Agent shares the Entra application OneDrive sync uses, but
`Files.Read.All` grants nothing over mail.
- **Impact:** a tenant that already syncs OneDrive will still get `403` on every mail call until `Mail.Read` and `Mail.Send` are granted and consented.
- **Mitigation in place:** the `403` is translated into a message that names the missing consent rather than leaving somebody debugging credentials that are in fact fine.
- **Priority:** High — but it is an administrator action, not code.

### Mail access is tenant-wide, and the mailbox is a single configured address
Application permissions carry no user, so `Mail.Send` lets the application send
as any mailbox it can reach, and `EMAIL_MAILBOX_ADDRESS` — not permission — is
what limits it to one.
- **Impact:** a configuration mistake could send from the wrong mailbox. Every Digital Twin in a deployment shares that one mailbox, so a draft written as one person is sent from the same address as one written as another.
- **Mitigation in place:** the mailbox is named explicitly in configuration and is never inferred, and no request body can change it. Sending always requires a human approval, so a wrong mailbox would be visible before the first message left.
- **Priority:** Medium — revisit alongside authentication, when a per-user mailbox becomes meaningful.

### Email attachment bytes live in the database
`email_attachment.content` is a `LargeBinary` column rather than a key into an
object store.
- **Impact:** attachments occupy database rows and backups, and a draft cannot carry a large file.
- **Mitigation in place:** `EMAIL_MAX_ATTACHMENT_BYTES` and `EMAIL_MAX_ATTACHMENTS_PER_DRAFT` bound it, and an attachment's whole life is "uploaded, handed to a provider at send time". An object store would be a second storage system, a second failure mode and a second thing to garbage-collect, for a payload that is already capped.
- **Priority:** Low — the column becomes a key when the cap starts hurting.

### Triage is one message at a time, and costs a model call each
There is no bulk triage and no background pass over an inbox.
- **Impact:** a large inbox has to be triaged row by row, on request.
- **Mitigation in place:** assessments are stored keyed by the provider's message id and updated in place, so re-opening the inbox costs nothing. An untriaged row is shown as "not yet triaged" rather than being guessed at.
- **Priority:** Low — a bulk pass is easy to add on top of `assess_message`, and should not be added before somebody has an inbox big enough to want it.

### OneDrive access is tenant-wide, not per-user
Synchronisation authenticates as the application, not as a person, so its
Graph permissions are granted once by an administrator and apply to every
folder they cover.
- **Impact:** the application can read more of the tenant than the folders in `ONEDRIVE_SOURCES`. Configuration, not permission, is what limits it.
- **Mitigation in place:** only configured folders are ever enumerated, and the client requests no write scope.
- **Priority:** Medium — revisit if the knowledge base ever holds material that is not company-wide.

### The scheduled sync assumes a single worker process
The periodic run is an asyncio task inside the application process. Two
workers means two timers.
- **Impact:** with more than one worker, a folder can be synchronised concurrently by each. Ingestion is idempotent, so the corpus stays correct, but the work is duplicated and two runs can race to write the same delta token.
- **Mitigation in place:** off by default; a single-worker deployment is unaffected.
- **Priority:** Medium — before running more than one worker with `ONEDRIVE_SYNC_ENABLED` set. A database advisory lock around a source is the smallest fix.

### Synchronisation runs in the request thread
`POST /sync/onedrive` downloads, parses and embeds before it responds, exactly
as upload does.
- **Impact:** a first sync over a large folder holds the request open for as long as the whole folder takes, and any proxy timeout in front of it will fire first.
- **Mitigation in place:** the work is resumable — re-running continues from the stored delta token — so a timed-out request loses the response, not the progress.
- **Priority:** Medium, and the same fix as background ingestion.

### Ingestion is synchronous, and now includes an embedding round trip
Parsing, chunking, embedding and persistence all happen inside the upload request.
- **Impact:** upload latency now includes a provider call, so it depends on network conditions and provider load as well as document size. A large document holds a request open for its whole processing time, and there is no progress reporting beyond the final status.
- **Mitigation in place:** embedding is batched at `EMBEDDING_BATCH_SIZE` texts per call rather than one call per chunk.
- **Priority:** Medium — the case for background ingestion is stronger than it was, per `DECISIONS.md`.

### Embedding cost is unmeasured
Every chunk of every upload is embedded, and re-uploading the same document embeds it again — there is no content-hash deduplication.
- **Impact:** cost scales with upload volume including duplicates, and nothing reports it.
- **Priority:** Medium — worth a look once real usage exists.

### `failed` has two meanings
A document can be `failed` because it could not be parsed (no chunks) or because it could not be embedded (chunks, no vectors). The status alone does not distinguish them.
- **Impact:** an operator has to check whether chunks exist to know whether a backfill will help.
- **Mitigation in place:** `count_unembedded_chunks()` answers it in one query, and the backfill is safe to run either way.
- **Priority:** Low — a distinct status would be clearer if this becomes common.

### Scanned documents are rejected, not OCR'd
A PDF with no text layer yields no extractable text and is recorded as `failed`.
- **Impact:** image-only PDFs cannot be ingested. The failure is explicit rather than silent, which is the intended behaviour, but it is still a gap.
- **Priority:** Low for the MVP; revisit if real uploads are frequently scanned.

### DOCX has no page numbers
Page boundaries in DOCX are a rendering property, so chunks from a DOCX carry a section heading but no page number.
- **Impact:** citations into DOCX documents will name a heading rather than a page.
- **Priority:** Low — accepted.

### `section_title` is a bounded label, not the whole heading
`section_title` is capped at `MAX_SECTION_TITLE_LENGTH` (200 characters) where the parser builds it. It is metadata: never embedded, never searched, used only for citations and the passage labels in chat context. The complete heading is always kept in the section's own text, so nothing is lost — a heading longer than the cap becomes a section in its own right rather than surviving only as a truncated label.
- **Impact:** a citation into a document whose heading runs to a paragraph names the first 200 characters. The cap also keeps the value inside `DocumentChunk.section_title`, which is `String(512)` — enforced by PostgreSQL and ignored by SQLite, so an over-long heading would otherwise fail an ingest that the whole test suite passes.
- **Priority:** Accepted by design. Measured against the corpus: 322 of 323 titles are under the cap, the median is 28 characters and the 99th percentile 91.

### Extraction quality is untested against real documents
`pypdf` handles well-formed PDFs. Multi-column layouts, tables and unusual encodings have not been exercised against anything but generated fixtures.
- **Impact:** unknown extraction quality on real customer material.
- **Priority:** Medium — the first thing to check with real uploads. If quality is poor, `unstructured` is the alternative to evaluate, at a significant dependency cost.

---

### A digest's window can be cut short by the provider page bound
`EmailProvider.list_messages` takes a limit and no date range — deliberately, because a date filter is expressible in exactly one vendor's query language and putting it in the boundary would leak Graph's dialect upward. So a digest asks for `DEFAULT_MESSAGE_LIMIT` (400) messages and filters them to the period. If that page is full *and* its oldest message still falls inside the window, earlier messages in the period are not counted. The digest reports `truncated: true` with an explanation rather than presenting the partial count as a total, so nothing is silently wrong — but the number is a floor, not a total. The fix is to widen the provider boundary to take a date range and implement it per provider, which is its own change.

### Report periods are UTC, not the reader's timezone
Every stored period is a UTC calendar week or month, so somebody in IST sees a week that begins at 05:30 their time. The alternative — a per-user timezone — would change what a *stored* period means when the user edited it, making historical reports retroactively cover different days. A fixed reference frame is what keeps history comparable, so it wins, and the cost is recorded here rather than hidden.

### The administrator bootstrap can be ambiguous at second resolution
With `BOOTSTRAP_ADMIN_EMAIL` unset, the earliest-created user is promoted, ordered by `created_at` then `id`. `created_at` comes from the database clock: on PostgreSQL that is the transaction timestamp at microsecond resolution and separate commits are genuinely ordered, but on SQLite it is second-resolution, so two users created in the same second sort by a random UUID. Set `BOOTSTRAP_ADMIN_EMAIL` for a deployment that needs the choice stated rather than inferred.

### The digest schedule shares the sync scheduler's single-worker assumption
`REPORT_DIGEST_SCHEDULE_ENABLED` starts a second `SyncScheduler` in the same process, so with more than one worker every worker runs its own timer — the same limitation recorded above for the sync. It matters much less here: the job is idempotent, the unique constraint on `(user_id, report_type, period_start)` makes a duplicate write impossible, and a losing racer re-reads the winner's row. The cost of the duplication is a wasted mailbox read, not a wrong report.

### Only completion is reconstructible for a past week
A historical work report answers "what did last week look like" from the columns a task carries. `created_at` says whether it existed and `completed_at` says whether it was still open, so those two questions are answered exactly. Every other transition — started, blocked, cancelled — leaves no timestamp, so a task unblocked on Thursday reads unblocked in every report of every earlier week. The report says so implicitly by never claiming otherwise; the alternative is a status-history table, which is a schema nobody has asked for and would change no number on the screen today.

### An enum column's width is set when its migration is written, not when its vocabulary grows
`sa.Enum(..., native_enum=False)` renders as `VARCHAR(n)` sized to the longest member at that moment. Add a longer member later and the column silently stays too narrow — and **SQLite ignores `VARCHAR` lengths**, so the whole test suite passes while PostgreSQL raises `value too long for type character varying(n)` the first time a person uses the feature. It has happened twice: `task.status` could not hold `in_progress` (the Start button returned a 500), and `documents.status` could not hold `unsupported`. Both are fixed in migration 0013, and `tests/database/test_migration_fidelity.py` now compares the migrated schema against the models on a real PostgreSQL so the next vocabulary change fails a test instead of a user. That test is skipped without a database, so the gap is narrowed rather than closed: a change merged with only the SQLite suite run can still reintroduce it.

### The inbox pages by size, not by cursor
`EmailProvider.list_messages` takes a count and returns a list. "Load more" therefore re-asks for a larger page rather than continuing from where the last one stopped, which is honest at fifty and a hundred and stops being so in the hundreds — the same messages are fetched again each time. A real cursor is a change to the provider contract (every implementation would have to carry one), so the page is bounded at `EMAIL_INBOX_MAX_PAGE_SIZE` instead and the follow-up is recorded in `ROADMAP.md`.

## Carried-forward decisions to revisit

### One prompt has no caller
The registered `assistant` prompt. **The Analysis Engine has left this list**: every Email Agent operation runs through it — composing and revising return validated `{subject, body}`, and triage returns a validated category, priority and action list. The LLM provider abstraction left when embeddings started using it, and the prompt system left when chat started rendering `rag_answer`.
- **Impact:** one registered template nothing renders.
- **Priority:** Low — it costs a dictionary entry. Remove it if it is still uncalled when the next feature lands.

### `langchain-text-splitters` carries more weight than it earns
Used for one function, `RecursiveCharacterTextSplitter`, and pulls a transitive tree considerably larger than that.
- **Impact:** dependency surface out of proportion to the feature.
- **Priority:** Low — it is used in exactly one place and is straightforward to replace.

### OpenRouter embedding support has not been exercised live
OpenRouter announced `POST /api/v1/embeddings` on 16 July 2026, which is the path the OpenAI SDK produces against the configured `base_url`. It is not yet listed in the API reference, and no live call has been made from this codebase.
- **Impact:** if the endpoint is not SDK-compatible in practice, ingestion fails at the embedding step with a `502` and the document is marked `failed`.
- **Mitigation in place:** `python -m scripts.verify_embedding_provider` makes one live call and reports the width. The fallback is `EMBEDDING_PROVIDER=openai` with `EMBEDDING_MODEL=text-embedding-3-small` — an `.env` change, no code change. Documents that failed can be completed with the backfill.
- **Priority:** High — run the verification script before the first real upload.

---

## Technical debt

### No CI pipeline
Nothing runs the test suite automatically.
- **Impact:** a broken change can be merged.
- **Note:** unlike the reference repository, the suite has no credential or service dependency, so adding CI is now unblocked — it needs `pip install -r requirements.txt` and `pytest`.
- **Priority:** Medium.

### No type checking in CI
`mypy` is not configured, though the codebase is fully annotated.
- **Priority:** Low.

### No structured logging
Nothing is logged. Ingestion failures are recorded on the document row, but there is no request or error log.
- **Priority:** Medium — before any deployment.

### No rate limiting on upload
`POST /documents/upload` accepts unbounded request volume; only per-file size is limited.
- **Priority:** Medium — before any public exposure.

---

## Resolved on migration from `ai-shadow`

These were open issues in the reference repository and were fixed while porting.

| Issue | Resolution |
|---|---|
| Misnamed and missing `__init__.py` files | Every package directory has a correct `__init__.py` |
| LLM client built at import, so tests needed live credentials | Client construction deferred to first use |
| `pytest` absent from `requirements.txt` | Declared, along with `python-multipart` |
| No `.env.example` | Added, with every variable documented |
| Hardcoded `echo=True` on the engine | Driven by `settings.DEBUG` |
| Settings with no defaults made the package unimportable | Every setting has a working default |
| `AnalysisValidationError` had no HTTP mapping | Exception handlers map all domain errors |
| `json.loads` failed on markdown-fenced model output | Fences stripped before parsing |
| `CHROMA_PATH` configured with no implementation | Removed |
| Three never-raised exception classes | Not carried forward |
| Four prompt templates with no caller | Not carried forward |
| Five empty service stub files | Not carried forward |
| No linter or formatter config | `ruff` configured for both |
