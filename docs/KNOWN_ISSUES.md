# AI Shadow MVP — Known Issues

Current gaps, limitations and accepted tradeoffs. For what exists, see [`FEATURES.md`](FEATURES.md).

---

## Limitations

### Live activity reports read 255-character previews, and counted unsent drafts as received mail
Found in the pre-deployment audit of 2026-09-29. `OutlookEmailProvider.list_messages` selected `bodyPreview` and never `body`, so every *live* report (`correspondence.from_provider`) analysed the first 255 characters of each message — while the collected corpus the pipeline had been validated on carries full bodies. A commitment made after the first paragraph was invisible to a production report. And `/users/{mailbox}/messages` spans every folder, Drafts included: an unsent draft has no `from`, so it entered the report as inbound mail from nobody.

A windowed listing (the report path; `since`/`until` given) now selects `body` and `isDraft`, pages at `WINDOW_PAGE_SIZE` (50, the size the collector reads this tenant with), and drops drafts. `correspondence` converts HTML bodies to text the same way the collector does. The un-windowed inbox listing is unchanged. Pinned by `tests/services/email/provider/test_windowed_listing.py`.

**Still open:** the all-folders read also includes Deleted Items and Junk Email, and a message a rule copied into a second folder is counted twice (different Graph ids, same `internetMessageId`). The live `/messages` listing itself has still never run against the tenant (the collector's per-folder reads have); the first post-deployment smoke test is its first real exercise.
- **Impact:** previews and drafts closed; deleted/junk/duplicate copies open (quality, not fabrication).
- **Priority:** Medium for the remainder.

### The activity report was a count, and the Digital Twin form could not save
Both closed in the same change, and both were the same kind of defect: a layer discarding what the layer above it needed.

**Reports.** `activity_report.py` assembled its sentences from an `EmailDigest`, which holds counts, a category tally and a list of correspondents. Message bodies and conversation ids were discarded two layers earlier, so the best possible report was a summary of counts — "200 received, 0 sent, 2 tasks open" — and no prompt could have fixed it because there was no model in the path at all. That module was a deliberate design, argued in its own docstring; it is now the fallback rather than the only path, and the evidence-based pipeline described in `FEATURES.md` is the default. The old deterministic report and both digest endpoints are unchanged.

**Digital Twin save.** `PUT /profile` answered `422 Value error, cannot be blank` when an existing twin was edited. The workspace form posts every text field on every save and its `organization` input has no fallback value, so any profile without one failed on a field the person had never touched. A blank required field now means "leave it alone"; the rule that a profile must name somebody moved to `profile_service.REQUIRED_ON_CREATE`, which is the only place that can tell a first write from an edit, and the form omits blanks and marks the three fields required on creation.
- **Impact:** closed.
- **Priority:** —

### The detailed activity report answered HTTP 500, and the screen hid it
Three defects, found together because the first one made the other two visible.

**The 500.** `report_routes.activity_report` decided whether to use a model with `bool(settings.OPENAI_API_KEY or settings.LLM_API_KEY)`. There is no `LLM_API_KEY` setting — `Settings` defines `OPENAI_API_KEY` and `OPENROUTER_API_KEY` — so pydantic raised `AttributeError` and FastAPI returned 500. It only fired once a mailbox was genuinely connected, because every request without one returns 409 several lines earlier, and the two route tests that existed took exactly that path. 1,608 passing tests never executed the line. The question is now asked of `llm_client.llm_configured()`, which reads the key belonging to the provider `LLM_PROVIDER` names, and `tests/api/test_report_routes.py::TestActivityReportWithAMailbox` runs the route end to end against a stub mailbox.

**The reports screen hid it.** `ReportsPanel` fell back to the stored email digest whenever the detailed report was absent, for any reason, so the 500 rendered as a small error line above "83 messages received and 0 messages sent" — a report-shaped screen that looked like it was working. The fallback is now keyed on *why* the request failed (`lib/reportFault.ts`): a 409 with `EmailProviderNotConfiguredError` is a configuration state and the digest stands in, saying so; anything else is a fault and nothing stands in for it.

**Half the correspondence was never read.** `correspondence.from_provider` looked for `provider.list_sent_messages` via `getattr`, which no provider implements and `EmailProvider` does not declare, so `None` came back and the outbound half was skipped silently. Every live report described the inbox and none of the owner's replies. Both directions now come from `list_messages(folder=...)`, which is the declared contract, and truncation is detected by whether the listing reached back past the start of the period rather than by a limit the provider is free to ignore.
- **Impact:** closed.
- **Priority:** —

### A Digital Twin's name lived in two tables and only one of them was written
`users.name` and `users.role` are what the workspace displays — the twin switcher, the sidebar, the workspace header, a report's owner line — and `digital_twin_profile.name` and `.role` are what the persona is written from. `PUT /profile` wrote only the second pair, so renaming a twin on its Profile tab saved successfully, changed nothing anybody could see, and survived a reload that way. `profile_service._apply_to_identity` now writes a supplied name or role onto the user record in the same transaction; a save that carries neither leaves both alone, which is what keeps a Preferences-tab edit from blanking an identity other rows point at. On the client, `TwinsPanel` holds the open twin's id rather than a copy of the row, and a saved profile re-reads the twin list, so the new name appears without a reload.
- **Impact:** closed.
- **Priority:** —

### The activity report took minutes, and the log said only that a batch had failed
One request, 18 model calls, no ceiling on any of them, and nothing in the log to say so. Four causes, all measured against a stub provider before being changed.

**Every conversation was analysed twice.** A month is reported week by week *and* as a whole, and each pass ran its own per-conversation analysis over the same threads. The weekly sections use only each week's synthesis, so the thread analysis now runs once over the period and each week is given the slice of it that belongs to it: 18 calls became 11 on the August corpus, with the same report at the end.

**Nothing bounded the total.** The calls are sequential, so response time was the provider's latency times the number of conversations. `REPORT_ANALYSIS_BUDGET_SECONDS` (90 by default) now bounds them together: when it is spent the remaining conversations are described structurally and the report says so in its header, which is the same degradation the pipeline already performs with no model configured. Measured against a provider answering in 30s: 90s instead of 9 minutes.

**Each call inherited the OpenAI SDK's defaults** — a 600-second read timeout and two automatic retries — so one unanswered call could hold a request for half an hour and a failing provider turned 18 calls into 54 HTTP attempts. `LLM_TIMEOUT_SECONDS` and `LLM_MAX_RETRIES` are now set on every client in `llm/client.py`.

**The log could not say which of these it was.** `report_thread_batch_failed` put the exception type in a `logging` `extra` dict that the default formatter does not print, and nothing configured the root logger at all, so every INFO line this codebase writes was discarded. `app/main.py` now configures logging from `LOG_LEVEL`, and the pipeline logs request start, period, message count, per-batch outcome, failed batch index, exception type and message, and elapsed time at each stage. Batch failures carry `exc_info`, so the traceback is in the log. No line carries a subject, a body or an address — `test_a_log_line_carries_no_correspondence` pins that.

Two smaller things came out of the same investigation. `AnalysisEngine` now distinguishes an empty model response — routine for a model that answers in a reasoning channel — from malformed JSON, and reads an object out of a reply that wrapped it in prose rather than losing the batch. And `scripts/diagnose_activity_report.py` runs one report against the live configuration and prints every model call, its timing, its exception and its cause, because a pipeline designed to degrade quietly cannot be diagnosed from its output.
- **Impact:** closed.
- **Priority:** —

### The Reports page ran the analysis more than once per visit
The effect that generates the detailed report was keyed on the period *object*. Every reload of the page's data — switching twin, changing the mailbox, re-reading the stored snapshot — produces an equal period with a fresh identity, so it re-ran a minutes-long pipeline for a question that had not changed, and with nothing tracking what was in flight, two could run at once and the slower one would win. It is now keyed on `user:reportType:period.key`, a superseded response is discarded rather than rendered, and Regenerate deliberately bypasses the guard because repeating the work is the point of it. `src/tests/reportsPanelRequests.test.tsx` mounts the real page in jsdom and counts the requests.
- **Impact:** closed.
- **Priority:** —

### The activity report was recomputed on every request, including for periods that had closed
`GET /reports/activity` was a pipeline with a URL. Every request read the mailbox, classified and threaded it, made a dozen model calls and composed a document — for a week that had ended a fortnight earlier and could not possibly have a different answer. Measured live: 7–13 September did not return after 200 seconds, and 14–20 September took about 60 for a single day of data. Nothing was stored, so the second reader paid the same price as the first, for the same document.

Closed by storing the report. `activity_report` holds one row per `(user_id, period_start, period_end, report_version)`; `activity_store` owns the reuse rules and `activity_service` the sequence. A closed window is answered from the database. A running one is served while fresh and otherwise revalidated against a fingerprint of the correspondence, which costs a mailbox read and no model calls when nothing has arrived. `POST /reports/activity` is the only rebuild.

Kept separate from `generated_report` deliberately, and the separation is the design rather than duplication. That table's whole rule is that a stored report is immutable and a row is either the report or an explanation of its absence; this one is keyed on an arbitrary window rather than a period key, carries a version, and needs a `generating` state, which is a claim on work in progress rather than a report. Putting that state into `report_status` would weaken the guarantee the digest table exists to make.
- **Impact:** closed.
- **Priority:** —

### No authentication
There is no auth layer. A caller states who they are with the `X-User-ID` header and is believed.
- **Impact:** any caller can act as any user. Do not store production data in it.
- **Mitigation in place:** the Digital Twin is genuinely user-scoped — `users` exists, profile and memory are UUID-keyed to it, and every query filters on that key — so adding auth is a change to where the identity comes from, not a migration or a backfill. The knowledge base is shared by design and is not affected.
- **Priority:** High — before real user data.

### Three files ended a sync with "Ingestion failed unexpectedly (ValueError)"
One systemic defect, not three, and PostgreSQL-only — which is why the whole
suite passed while `PIM_Functional_Processes_v0.95.html`,
`Solution Delivery Roadmap _v0.05.html` and `Summary View.pdf` could not be
ingested. **PostgreSQL's `text` cannot hold U+0000**, and psycopg2 refuses the
parameter with `ValueError: A string literal cannot contain NUL (0x00)
characters` — an error from outside `DocumentError`, so it surfaced as the
uninformative "Ingestion failed unexpectedly (ValueError)". SQLite stores NULs
without complaint. Same invisibility class as the VARCHAR widths (0013) and the
INTEGER width (0014).

Two things produced the NULs, and both are fixed:

1. **Encoding detection could not fail safely.** The two HTML files are
   MindManager exports in UTF-16LE with a `\xff\xfe` byte-order mark. `_decode`
   tried `utf-8-sig`, `utf-8`, then `cp1252` — and `cp1252` maps almost every
   byte, so it did not raise. It returned mojibake beginning `ÿþ<\x00h\x00t…`
   with 19,741 NULs in it. `_decode` now honours byte-order marks before
   guessing, and recognises BOM-less UTF-16 by its NUL pattern.
2. **Nothing sanitised extracted text.** `Summary View.pdf` has no encoding
   problem at all: it is an ordinary readable six-page PDF whose font's
   ToUnicode CMap maps a handful of glyphs to U+0000, giving thirteen stray
   NULs. Control characters are now stripped once, at the `parse_document`
   boundary, so no format can bypass it — tab, newline and carriage return
   kept, everything else in C0/C1 dropped.

Verified against a real PostgreSQL 16: the reported `ValueError` reproduces on
an unsanitised insert, and both files now parse to clean text and index.
- **Impact:** closed. The class is closed too — sanitising is at the boundary rather than per-format.
- **Priority:** —

### A dropped read failed a file that would have succeeded
`Sun Radia Capabilities Case Studies and Services.pptx` failed on an
`httpx.ReadError`. The client retried 429, 503 and 504 and nothing else, so the
most literal "not now" there is — no status came back at all — was the one
condition never retried. One hiccup on one file marked an 800-file run
`partial` and withheld its delta token, paying for a re-enumeration. Transport
errors are now retried under the same budget and backoff as throttling; if the
budget runs out the failure is raised `transient=True`, because the bytes were
never seen.
- **Impact:** closed.
- **Priority:** —

### 30 files are scans, and are deferred rather than failed
Supported format, successful download, parser ran to completion, no text in the
document. These were counted as application failures, which inflated the
failure count of a healthy run and hid a backlog whose only remedy nobody can
apply by retrying.

They now have a status of their own — `ocr_required`, tagged
`OCR_REQUIRED / NO_TEXT_LAYER` in the reason — distinct from `failed` (which
means something went wrong and a retry may work) and from `unsupported` (which
means no parser exists for the format). Each leaves a durable row, so "which
documents need OCR" is a query; the sync counts them apart from failures, so a
corpus of scans no longer blocks acceptance; and the delta token still advances
over them, because they were genuinely dealt with. Migration 0015 adds the
status, widens `documents.status` to `VARCHAR(12)`, **rewrites the
`document_status` CHECK constraint** — the half that is easy to miss, and that
`tests/database/test_migration_fidelity.py` now guards generically — and adds
`onedrive_sync_state.last_ocr_required`.
- **Impact:** OCR is deliberately not implemented. These documents are listed, not indexed, and `scripts/diagnose_onedrive_corpus.py` names them.
- **Priority:** Low — a known backlog with a known remedy, tracked in `ROADMAP.md`.

### A whole live run indexed nothing, and called itself a success
The corrected sync ran over 859 items: 372 rejected before download, **487
downloads failed, 0 files indexed** — and it reported `succeeded` and advanced
its delta token past all 487. Two defects, and the second is the dangerous one.

1. **The downloader was broken.** `fresh_download_url` asked for the item with
   `$select=id,@microsoft.graph.downloadUrl`. That is an *instance annotation*,
   not a property, and Graph does not reliably return it under `$select`, so
   the lookup yielded an item with no URL — which the code called a permanent
   failure. Every file. Fixed by asking `/content` for its `302` and reading
   `Location` without following it, which is defined for every file item and
   leaks no bearer token; the annotation remains as a fallback.
2. **A run that read nothing was trusted anyway.** Advancing the token on "no
   transient failures" buried the whole corpus, because a delta only ever
   returns what changed *after* the token. The rule now also refuses to advance
   when downloads failed and not one succeeded, on the reasoning that a
   downloader breaking is far likelier than that many files each being
   independently unreadable. A corrupt PDF still counts as read — its bytes
   arrived.

`succeeded` now means every discovered file was dealt with; any failure makes
the run `partial`. And every failed download leaves a `failed` document row
carrying the filename, declared type, reported size and Graph's own reason, so
"which files failed, by extension, by MIME type, by reason" is a query rather
than a guess. `scripts/diagnose_onedrive_corpus.py --probe` answers the same
questions against the live drive without downloading anything.

There was a third, quieter defect: **the validation script agreed with the
sync.** It treated `succeeded` as proof of ingestion, and its retrieval stage
counted the whole corpus — which already held 268 documents from an earlier
staged import — so a run that read nothing scored three passes and exited 0.
Both are closed. A run that discovered files and successfully read none of them
is now reported as `INGESTION: FAILED` and votes the exit status non-zero, and
the retrieval stage splits "written during this run" from "already present
beforehand" and says plainly when everything it found predates the run.
`tests/scripts/test_operational_scripts.py` pins both, along with the
diagnostic's exit status, without needing a tenant.
- **Impact:** the knowledge base still holds only the earlier staged corpus. The delta token advanced once while broken, so **a full resync (`--full`) is required** — an incremental run would never re-offer those 487 files.
- **Priority:** High — the fix is in, and it has not yet been run live.

### The corpus is partly ingested, and the first real run exposed three defects
A real synchronisation has now run against the live tenant. It discovered all
847 files and ended `partial` after roughly 25 hours: 94 indexed, 41 unchanged,
292 unsupported, 420 failed, with the delta token withheld. Three defects
compounded to produce that, and all three are fixed:

1. **Every pre-authorised download URL was minted during enumeration.** Graph
   gives them about an hour; the run spent 25. Every file reached after the
   first hour answered 403 and was recorded as a failure.
2. **A 403 was classified transient.** So was every other download failure —
   `transient=True` was hard-coded — which withheld the delta token, so the
   next run would have re-enumerated all 847 files and done it again.
3. **Nothing was rejected before being downloaded.** All 292 unsupported files
   were fetched in full and then refused on their extension.

The wall-clock was serial downloads plus an unbounded retry budget: five
attempts each honouring a 60-second `Retry-After` is five minutes inside one
file.
- **Impact:** the knowledge base holds 135 of 847 files. The counts above are the *old* classification, so the supported/unsupported split will move once a run uses the new parsers — `.xlsx` and `.html` were counted as unsupported and are now read.
- **Mitigation in place:** URLs are re-minted once on 401/403; failures are classified by status and only genuinely transient ones hold the token; unsupported formats are decided from the listing and never downloaded; downloads run in bounded concurrent batches; retries stop on a total time budget; and a `partial` run now records the causes and their counts rather than only a total.
- **Priority:** High — the next run is expected to finish in well under an hour and to advance the token. **It has not been run**; every claim here is from the code and its tests, not from a second live sync.

### The exact HTTP statuses behind the first run's 420 failures were not recorded
Per-file reasons existed in the `POST /sync/onedrive` response body and in the
application log, but `onedrive_sync_state.error_message` persisted only
`"405 file(s) failed transiently; delta token not advanced."` — so a run nobody
watched left no way to tell an expired URL from a throttled one.
- **Impact:** the 405-vs-15 split between transient and permanent in that run cannot be attributed to specific statuses after the fact. The expired-URL diagnosis rests on the code path — URLs minted at enumeration, a 25-hour run, 403 not retried — and on 427 files having been fetched before the failures began, not on a recorded status.
- **Mitigation in place:** `_describe_failures` now writes the distinct reasons and their counts into `error_message`, capped at the five most common, so the next `partial` run is self-explaining.
- **Priority:** Resolved for future runs; the first run's detail is gone.

### The Knowledge Base needs a user principal name that cannot be derived
Largely resolved. The folders were first supplied as shortened `1drv.ms`
sharing links, which hide which service holds the content — the fact that
decides whether an application-only token can reach them at all. An in-tenant
address has since been supplied
(`sunradia-my.sharepoint.com/personal/sudha_gummuluru_sunradia_com/…/Knowledge
Base`), so the content is a SunRadia-managed OneDrive for Business location and
`Files.Read.All` covers it. One gap remains: a personal site name replaces
every `@` and `.` with `_`, so `sudha_gummuluru_sunradia_com` cannot be turned
back into an address.
- **Impact:** the `user` field in `ONEDRIVE_SOURCES` has to be filled in by somebody who knows the address. Guessing it would resolve to a different person's drive, or to none, and neither failure announces itself.
- **Mitigation in place:** `python -m scripts.discover_onedrive_sources --url "<url>"` reads the address offline and prints the entry to paste, with `<user>@example.com` left as a placeholder rather than a guess, and the `Documents` library prefix already stripped — keeping it produces a 404 on a folder that plainly exists. `--diagnose` still classifies any remaining share link, and `outside_tenant` is decided from the host without asking Graph, precisely so its `403` cannot be misread.
- **Priority:** High — it gates the corpus, and it is now one address away.

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

### Calendars.Read has not been granted
The first live check returned Graph `403 ErrorAccessDenied` for
`GET /users/{mailbox}/calendar`. `Calendars.Read` is a third application
consent on the same Entra application; neither `Files.Read.All` nor `Mail.Read`
covers it.
- **Impact:** `POST /calendar/import` cannot run, so reports show the calendar as not read — which is true, and is deliberately distinct from showing a free week.
- **Mitigation in place:** `GET /calendar/status` reports it without raising and names `Calendars.Read` specifically, saying that re-granting the other two cannot help. The client no longer names a permission it cannot know about: it serves three APIs with three consents and now reports the status and code, leaving the naming to the layer that knows which call failed.
- **Priority:** High — one administrator action, no code change.

### No calendar payload has ever been parsed
The tenant has now been reached and refused, so the *connection* is understood —
but no Graph calendar payload has passed through `to_event`, because the 403
comes before any event is returned.
- **Impact:** every request shape and every mapping here is asserted against constructed payloads. Recurring-series expansion through `/calendarView` and Graph's `{dateTime, timeZone}` pair are the two most likely places for a surprise, and neither can be settled until consent is granted.
- **Mitigation in place:** the refused state is a supported, tested state rather than a failure. The reports distinguish three states — not read, read and empty, read with meetings — so an unread calendar can never display as a free week, and `POST /calendar/import` refuses with a `503` rather than returning an empty result that would look like a quiet week.
- **Priority:** High, but blocked behind the consent above.

### Nothing imports the calendar on a schedule
`POST /calendar/import` is explicit and per-user. A report shows the meetings
that have been imported, so a report generated before anybody imports a window
says the calendar was not read — which is true, and is not the same as saying
the week was free.
- **Impact:** somebody has to press Import, or call the endpoint, for a period's meetings to appear in that period's report.
- **Mitigation in place:** the weekly work view has the button, and it imports exactly the window the report covers. `calendar_connected` is answered from rows rather than from consent, so the report never claims a calendar it has not actually received.
- **Priority:** Medium — scheduling it is a few lines in `SyncScheduler`, deliberately not added until somebody wants it, because an automatic import is the first step toward the autonomous behaviour this MVP excludes.

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

### A rejected file's recorded size is bounded by the drive, not by the upload limit
`documents.file_size_bytes` holds the size a file *reported*, not the size of
something this application accepted — a synchronised file rejected for being too
large is still recorded, so the corpus can say what it is missing. The first
real sync met a 6,788,146,857-byte Teams recording in the Knowledge Base folder,
three times what `INTEGER` holds, and `psycopg2.errors.NumericValueOutOfRange`
aborted the transaction and ended the run.
- **Impact:** one unwritable row ended a whole source's run, not just that file. The column is `BIGINT` from migration 0014, so this instance is closed, but the general shape remains: any database-level error on one file still aborts that source, because the failure comes from the transaction rather than from the file. Per-file guards cover downloads and ingestion, not the database.
- **Mitigation in place:** migration 0014 widens the column, and `tests/database/test_migration_fidelity.py` now checks integer widths against the models as well as string widths — SQLite's INTEGER is 64-bit, so no SQLite test can see this class of bug. The size is stored whole; clamping it to fit would be a lie in the one column whose purpose is to report how big the unreadable thing was.
- **Mitigation in place (second half):** the run is no longer left claiming `running`. `running` is committed before the work starts, so an exception escaping `sync_source` used to leave the row that way — and `db.rollback()` cannot undo a committed write, so the source refused every sync for the six hours `_is_stale` takes to forgive it. `_abandon` now releases it, records how far the run got, and leaves the delta token alone.
- **Priority:** Low for the widening and the release (both done). Medium for the general case — a per-file `SAVEPOINT` would let the loop continue past a database error rather than ending the source, and has not earned its place until a second instance appears.

### Legacy Office binaries are recognised and refused, not parsed
`.doc`, `.xls`, `.ppt`, `.mmap`, `.msg`, `.vsd` and `.one` are listed in
`KNOWN_UNSUPPORTED_EXTENSIONS` and rejected with the conversion that would fix
them. Reading them properly needs a heavyweight dependency — `antiword`,
`libreoffice`, `textract` — and half-reading one is worse than refusing it: a
partial extraction of an OLE2 binary is a document of wrong characters that is
then embedded, retrieved and cited as though it were the text.
- **Impact:** a corpus with many legacy Office files has a real gap, visible as `unsupported` rows carrying the reason. `.xls` is the sharpest edge — `xlrd` still reads it and is not a dependency here.
- **Mitigation in place:** each refusal names the conversion, so the list of rejects is a list of tasks. A synced file leaves a row, so the gap is countable rather than invisible; converting a file changes its `source_version` and the next run picks it up.
- **Priority:** Medium — reconsider `xlrd` for `.xls` if the corpus turns out to hold many.

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
