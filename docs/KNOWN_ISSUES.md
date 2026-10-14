# AI Shadow MVP — Known Issues

Current gaps, limitations and accepted trade-offs, grouped by area. For what
exists, see [`FEATURES.md`](FEATURES.md); for what is planned, see
[`ROADMAP.md`](ROADMAP.md). Defects that have been fixed are summarised at the
end so their lessons are not lost.

---

## Security and access

### No authentication
There is no auth layer. A caller states who they are with the `X-User-ID`
header and is believed; the frontend's twin selector sets it.
- **Impact:** anyone who can reach the application can act as any user and read
  that user's profile, memories, drafts, tasks, reports and mailbox contents.
- **Mitigation in place:** all private data is genuinely user-scoped, so adding
  auth changes only where the identity comes from. In production,
  `ORIGIN_SECRET` blocks calls that bypass the Vercel proxy — a perimeter
  check, not user authentication.
- **Priority:** High — before sensitive data or wider rollout.

### Microsoft Graph permissions are tenant-wide
The application authenticates as itself (client credentials). `Mail.Send` lets
it send as any mailbox it can reach, `Mail.Read` read any mailbox, and
`Files.Read.All` read any file in the tenant.
- **Impact:** configuration, not permission, limits what is touched: each
  twin's stored mailbox address and the folders in `ONEDRIVE_SOURCES`.
- **Mitigation in place:** mailboxes are per user and never inferred; sending
  always requires human approval; only configured folders are enumerated; no
  write scope is requested for files.
- **Priority:** Medium — revisit alongside authentication.

---

## Knowledge base and ingestion

### Ingestion and synchronisation run on the request thread
Upload parses, chunks and embeds before responding; `POST /sync/onedrive` does
the same for a whole folder.
- **Impact:** large documents and first syncs hold a request open and can hit
  proxy timeouts; upload latency includes a provider call.
- **Mitigation in place:** embeddings are batched; sync is resumable from the
  stored delta token, so a timed-out request loses the response, not the
  progress.
- **Priority:** Medium — background ingestion is on the roadmap.

### Legacy Office binaries are refused, not parsed
`.doc`, `.xls`, `.ppt`, `.mmap`, `.msg`, `.vsd`, `.vsdx`, `.one` and `.zip` are
rejected with the conversion that would fix them
(`KNOWN_UNSUPPORTED_EXTENSIONS`). Half-reading an OLE2 binary would produce a
document of wrong characters that is then cited.
- **Impact:** a corpus gap, visible as `unsupported` rows for synced files.
- **Priority:** Medium — reconsider `xlrd` for `.xls` if many appear.

### Scanned documents need OCR, which is not implemented
A supported file with no text layer is recorded `ocr_required`
(`OCR_REQUIRED / NO_TEXT_LAYER`), counted apart from failures, and listed by
`scripts/diagnose_onedrive_corpus.py`. About 30 exist in the SunRadia corpus.
- **Priority:** Low — tracked in `ROADMAP.md`.

### An edited manual upload becomes a new document
Identity is the content hash (plus `source_uri` for synced files). A re-uploaded
edit has new bytes and no source, so the old version stays.
- **Impact:** stale and current versions can both be retrieved. Retrieval
  collapses identical passages but not differing versions.
- **Mitigation in place:** delete the old document; OneDrive-sourced files are
  re-indexed in place.
- **Priority:** Low.

### Documents ingested before content hashing carry `content_hash = NULL`
They never compare equal to a digest, so they are never deduplicated; the
original bytes were not stored, so they cannot be backfilled.
- **Priority:** Low — a document gains an identity the next time it is uploaded.

### `failed` has two meanings
A document is `failed` if it could not be parsed (no chunks) or not embedded
(chunks, no vectors).
- **Mitigation in place:** `count_unembedded_chunks()` distinguishes them, and
  `python -m scripts.backfill_embeddings` is safe either way.
- **Priority:** Low.

### Extraction quality on complex layouts is unmeasured
`pypdf` handles well-formed PDFs; multi-column layouts, tables and unusual
encodings have not been systematically evaluated on real material.
- **Priority:** Medium.

### DOCX has no page numbers; `section_title` is a bounded label
Page boundaries are a rendering property, so DOCX citations name a heading.
`section_title` is capped at 200 characters (the full heading stays in the
passage text).
- **Priority:** Accepted.

### Embedding cost is unmeasured
Identical re-uploads are skipped by hash, but nothing reports embedding volume
or cost.
- **Priority:** Low.

---

## OneDrive synchronisation

### A complete resync with the corrected sync code has not been recorded
The first live run discovered 847 files and ended `partial`; a later run
advanced its delta token while its downloader was broken. Both defects are
fixed (see the resolved summary), but because a delta only returns changes
after the token, files skipped by that run are only re-offered by a full
enumeration.
- **Impact:** the Knowledge Base may be missing synced documents until a
  `full: true` run completes.
- **Priority:** High — an operational step, listed in `ROADMAP.md`.

### A database error on one file aborts that source's run
Per-file guards cover downloads and ingestion, but an exception from the
transaction itself (as the 6.8 GB file-size overflow once was) ends the source.
- **Mitigation in place:** the source is released from `running` and its delta
  token left untouched; `test_migration_fidelity.py` guards column widths.
- **Priority:** Medium — a per-file `SAVEPOINT` would contain it.

### Source status is per-run, not cumulative
`onedrive_sync_state` holds the last run's counts; corpus size comes from
`GET /documents/stats`.
- **Priority:** Low.

### Schedulers assume a single worker process
`ONEDRIVE_SYNC_ENABLED` and `REPORT_DIGEST_SCHEDULE_ENABLED` start asyncio
timers in the application process; each worker runs its own.
- **Impact:** duplicated work with more than one worker. Ingestion and digests
  are idempotent, so data stays correct.
- **Mitigation in place:** both are off by default and in `render.yaml`
  (one instance).
- **Priority:** Medium — before enabling with multiple workers.

### A source's `user` principal name cannot be derived from a personal site URL
`sudha_gummuluru_sunradia_com` cannot be turned back into an address reliably.
`discover_onedrive_sources --url` leaves a placeholder rather than guessing.
- **Priority:** Low — a one-time configuration step.

---

## Email and calendar

### Sending has not been verified live from this repository
Mail reading is exercised live; a send through `Mail.Send` is not recorded.
- **Mitigation in place:** sending requires approval and `confirm: true`; a
  provider refusal marks the draft `failed` with the reason.
- **Priority:** Medium — one supervised test send.

### `Calendars.Read` has not been granted
Graph returns `403 ErrorAccessDenied`, so `POST /calendar/import` cannot run and
no live calendar payload has been parsed. Mail and file consents do not cover
it.
- **Mitigation in place:** `GET /calendar/status` names the missing permission;
  reports distinguish "calendar not read" from "no meetings".
- **Priority:** High — one administrator action.

### Calendar import is manual only
Someone must press **Import from Outlook** (or call the endpoint) for meetings to
appear. Automatic import is deliberately excluded as autonomous behaviour.
- **Priority:** Medium.

### The report's mailbox read spans all folders
The inbound half of an activity report reads `/messages` (all folders, drafts
excluded), so Deleted Items and Junk Email are included, and a message copied
into a second folder by a rule is counted twice.
- **Impact:** report quality, not fabrication.
- **Priority:** Medium.

### Triage is one message at a time
Each assessment is a model call; there is no bulk or background triage.
Assessments are stored and reused.
- **Priority:** Low.

### The inbox pages by size, not by cursor
"Load more" re-requests a larger page, bounded at `EMAIL_INBOX_MAX_PAGE_SIZE`
(200). A cursor is a provider-contract change.
- **Priority:** Low.

### Smaller email gaps
- Attachments on **received** mail are listed but cannot be downloaded through
  the API.
- Draft attachment bytes are stored in the database row (bounded by
  `EMAIL_MAX_ATTACHMENT_BYTES` and `EMAIL_MAX_ATTACHMENTS_PER_DRAFT`).
- Sent mail gets no provider message id (Graph's send calls return none).
- A process crash mid-send can leave a draft in `sending`.
- Bodies are sent as plain text.
- **Priority:** Low.

---

## Reports

### Model credit and rate limits affect generation
Activity reports, chat, triage and drafting depend on OpenRouter. Low credit or
concurrent requests can return HTTP 402 (`in_flight_budget_exhausted`) or other
provider errors.
- **Mitigation in place:** `max_tokens=4096` per call bounds each reservation;
  provider errors are 502s; reports share a 90-second model budget and fall back
  to structural analysis.
- **Priority:** Medium — operational (credit, concurrency).

### Report download is the stored digest
**Download** on the Reports screen exports the stored weekly/monthly summary as
`.docx`, not the detailed activity analysis shown on screen. No other format
exists.
- **Priority:** Low.

### Accepted reporting trade-offs
- **Periods are UTC**, so a week begins at 05:30 IST; a fixed frame keeps stored
  history comparable.
- **A digest's window can be cut short** by the provider page bound; it then
  reports `truncated` rather than presenting a partial count as a total.
- **Only completion is reconstructible for a past week** — tasks carry
  `created_at` and `completed_at`, not a status history.

---

## Data model and operations

### Enum columns carry no database CHECK constraint
`sa.Enum(..., native_enum=False)` emits no constraint since SQLAlchemy 1.4.
Application writes are validated; out-of-ORM writes are not.
- **Priority:** Low.

### An enum column's width is fixed when its migration is written
A longer member added later can overflow `VARCHAR(n)` on PostgreSQL while SQLite
ignores it (this happened twice; fixed in migration 0013).
`tests/database/test_migration_fidelity.py` catches it, but only when run
against PostgreSQL.
- **Priority:** Low.

### The administrator bootstrap can be ambiguous on SQLite
With `BOOTSTRAP_ADMIN_EMAIL` unset, the earliest user is promoted; SQLite
timestamps are second-resolution. Set the variable in deployments.
- **Priority:** Low.

### Frontend gaps
- Chat history is browser-local, and each question is answered without earlier
  messages as context.
- **Prepare escalation email** in the weekly work view does not open a draft.
- No re-index button; no UI to retire a memory (API only).
- **Priority:** Low.

---

## Technical debt

- **No CI.** The suite needs no credentials or services, so adding CI is
  unblocked. *Medium.*
- **No type checking** (`mypy`) although the code is fully annotated. *Low.*
- **Plain-text logging.** Logging is configured from `LOG_LEVEL` and the report
  pipeline logs its stages, but logs are not structured and there is no request
  log. *Medium.*
- **No rate limiting** on upload or model-backed endpoints. *Medium.*
- **One prompt has no caller** — the registered `assistant` prompt. *Low.*
- **`langchain-text-splitters`** is a large dependency for one function. *Low.*

---

## Resolved defects (summary)

Kept for the lessons they carry; details are in the git history.

| Defect | Root cause | Fix |
|---|---|---|
| Greetings and "introduce yourself" returned the no-match reply | Every message went to retrieval; empty results short-circuited before the profile loaded | Intent routing (`chat/intent.py`), `twin_conversation` prompt, `answer_basis` |
| Provider failures surfaced as unlogged 500s | OpenAI SDK errors were not wrapped | `LLMService` raises `LLMServiceError` (502), logs class and status only |
| OpenRouter 402s from oversized output reservations | No explicit output limit | `max_tokens=4096` on every completion |
| Render startup failed with `No module named 'psycopg'` | SQLAlchemy 2.1 maps `postgresql://` to psycopg 3 | `psycopg[binary]` added; driver test for each URL form |
| Live reports read 255-character previews and counted drafts as received | Listing selected `bodyPreview`; `/messages` includes Drafts | Windowed listing selects `body` and `isDraft`, drops drafts |
| Detailed report returned 500 and the screen hid it | A non-existent `LLM_API_KEY` setting; fallback rendered for any failure | `llm_configured()`; fallback keyed on the failure reason (`reportFault.ts`) |
| Reports described only inbound mail | A `list_sent_messages` method no provider has | Both directions via `list_messages(folder=...)` |
| Reports took minutes and logged nothing useful | Duplicate per-week analysis, no budget, SDK default timeouts, no logging config | Single analysis pass, `REPORT_ANALYSIS_BUDGET_SECONDS`, client timeouts/retries, `LOG_LEVEL` logging |
| Closed periods were recomputed on every request; the page ran analyses twice | No storage; effect keyed on an object identity | `activity_report` table (0016); request keyed on period |
| Twin rename and profile save failed or did nothing | Blank required field rejected on edit; name stored in two tables | `REQUIRED_ON_CREATE`; name/role written to `users` too |
| Sync: expired download URLs, 403 treated as transient, unsupported files downloaded | URLs minted at enumeration; hard-coded `transient`; no pre-download decision | Re-mint on 401/403; status-based classification; reject from metadata |
| A sync indexed nothing and reported success | Download URL lookup via `$select` annotation; token advanced on a blind run | `/content` redirect; no token advance when every download failed |
| `ValueError` on NUL characters in three files | UTF-16 decoded as cp1252; stray NULs in a PDF | BOM-aware decoding; control characters stripped at the parser boundary |
| A dropped connection failed a readable file | Transport errors not retried | Retried under the throttling budget |
| Scans counted as failures | No distinct status | `ocr_required` (migration 0015) |
| 6.8 GB file overflowed `file_size_bytes` | `INTEGER` column | `BIGINT` (0014); width checks in migration-fidelity test |
| `task.status` / `documents.status` too narrow on PostgreSQL | Enum width fixed at migration time | Widened (0013); fidelity test |
| A 403 was blamed on `Sites.Read.All` | Over-specified permission advice | Messages name `Files.Read.All` only |
| OpenRouter embeddings unverified | New endpoint | Exercised live; `verify_embedding_provider` remains the check |

### Resolved on migration from `ai-shadow`

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
