# AI Shadow MVP

Upload documents, index them, and — once retrieval lands — chat with them and get answers with citations.

This repository is the focused MVP build. It reuses the components from the original `ai-shadow` prototype that earn their place (LLM provider abstraction, prompt system, Analysis Engine, configuration and testing patterns) and leaves behind the parts that were designed but not needed: the orchestrator, memory system, research and calendar tools, and a set of empty service stubs.

**The pipeline is complete end to end: upload → index → embed → retrieve → answer.** Chat is stateless; conversation memory is not built. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Quick start

```bash
# 1. Start Postgres with the pgvector extension available
docker compose up -d

# 2. Install dependencies
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure
cp ../.env.example .env            # defaults match docker-compose

# 4. Create the schema
alembic upgrade head

# 5. Confirm the embedding provider actually serves embeddings
python -m scripts.verify_embedding_provider

# 6. Run
uvicorn app.main:app --reload
```

Step 5 makes one live call and prints the returned vector width. If it fails, set `EMBEDDING_PROVIDER=openai` and `EMBEDDING_MODEL=text-embedding-3-small` in `.env` — no code change is needed.

Interactive API documentation is served at `http://localhost:8000/docs`.

Run the tests with `pytest` from the `backend/` directory. The suite uses an in-memory SQLite database and needs no running services or API credentials.

A handful of retrieval tests run the search query against real pgvector to confirm the SQLite stand-in agrees with it. They are skipped unless a database is reachable; to include them, bring up `docker compose`, run `alembic upgrade head`, then `pytest -m postgres`. They work inside a rolled-back transaction and leave nothing behind.

---

## What ingestion does

```
Upload  →  Validate  →  Extract text  →  Chunk  →  Embed  →  Persist
           size            PDF: per page   configurable  batched    documents
           type            DOCX: headings  size and      provider   document_chunks
           emptiness       MD: headings    overlap       calls      + vectors
                           TXT: whole
```

Chunks are stored with the page number and section heading they came from, which is what makes a citation resolvable back to a specific place in a specific document later.

A document reaches `status="indexed"` only once every chunk carries a vector. That matters because a document without vectors is invisible to similarity search, and would otherwise surface to the user as "nothing relevant found" rather than as a failure.

### Building the knowledge base

A real corpus is not all worth indexing: duplicates, superseded reissues, contact lists, pricing, other firms' material. Two scripts turn one into a knowledge base without touching the source folder.

```bash
cd backend
python -m scripts.build_kb_manifest --corpus "D:/ai-shadow-knowledgebase"
python -m scripts.ingest_kb_manifest              # --dry-run first, if you like
python -m scripts.ingest_kb_manifest --verify-only
```

The first walks the corpus read-only and writes `knowledge_base_manifest.json`: every file is recorded as included, excluded or needing review, with the reason, its category, its brand, its year, and — where it applies — the file it duplicates or the file that superseded it. Years come from the filename or folder, never from the modification time, which OneDrive rewrote across this corpus. Running it twice on the same corpus produces byte-identical output, so the selection is reviewable rather than remembered.

The policy is inclusive: everything first-party and readable is selected unless a rule excludes it, and there are no per-category limits. A file is only dropped for being unreadable today, sensitive, a template or form, another organisation's material, a byte-identical duplicate, or a superseded issue of a document that is also selected. Anything sensitive-looking but ambiguous goes to a third bucket, `review`, and is neither ingested nor silently dropped — read those and pass `--include-review` if they belong.

The manifest also reports what it had to leave behind, by format, with the largest candidates named. That list is the honest measure of how complete the knowledge base is: legacy `.doc`, `.ppt` and `.vsd` files hold real material the parser cannot read yet.

The second script sends only the selected files through `POST /documents/upload`, skips anything already indexed so a re-run resumes rather than duplicates, continues past any individual failure, then checks the database: what is indexed, what has chunks, whether any chunk is missing its vector, whether the same filename arrived twice, whether anything is present that the manifest never asked for. Results land in `knowledge_base_ingestion_results.json` — a run artifact, not committed — and the exit code is non-zero if anything failed.

### Documents that need embedding

Documents ingested before embedding existed, and uploads whose embedding call failed, have chunks but no vectors:

```bash
cd backend && python -m scripts.backfill_embeddings
```

Safe to re-run — chunks that already have a vector are skipped.

---

## API

All endpoints are unauthenticated for now, and every stored row is scoped to a single placeholder owner. See [Security](#security) below.

### `POST /documents/upload`

Ingest one document. Accepts `multipart/form-data` with a single `file` part. Supported formats are PDF, DOCX, TXT and Markdown; the declared content type is used first and the file extension is a fallback.

Processing is synchronous, so the response describes the final state rather than a queued job.

```bash
curl -X POST http://localhost:8000/documents/upload \
     -F "file=@quarterly-report.pdf"
```

```json
{
  "id": "9f1c2b6e-3a5d-4f18-9c77-0b3f2a4e5d61",
  "filename": "quarterly-report.pdf",
  "content_type": "application/pdf",
  "file_size_bytes": 248113,
  "page_count": 12,
  "chunk_count": 47,
  "status": "indexed",
  "error_message": null,
  "created_at": "2026-07-28T09:14:22.114Z",
  "updated_at": "2026-07-28T09:14:23.882Z"
}
```

| Status | When |
|---|---|
| `201` | Ingested successfully |
| `413` | File exceeds `MAX_UPLOAD_SIZE_BYTES` |
| `415` | Unsupported format |
| `422` | Empty file, no extractable text, unreadable file, or no `file` part |
| `502` | The embedding provider failed or returned the wrong vector width |

A file that passes validation but fails to parse is stored with `status: "failed"` and an `error_message`, and returns `422`. A file rejected by validation is not stored at all.

A file that parses but cannot be embedded is also stored as `failed` — but its chunks are kept, so `scripts/backfill_embeddings.py` can finish the job once the provider recovers, without a re-upload.

### `GET /documents`

List documents, newest first.

| Query parameter | Type | Default | Notes |
|---|---|---|---|
| `status` | `pending` \| `processing` \| `indexed` \| `failed` | — | Optional filter |
| `limit` | integer, 1–200 | `50` | Page size |
| `offset` | integer, ≥ 0 | `0` | Page offset |

```json
{ "items": [ /* documents */ ], "total": 17, "limit": 50, "offset": 0 }
```

Returns `200`, or `422` for an out-of-range `limit` or `offset`.

### `GET /documents/stats`

Corpus-wide counts, computed in the database rather than by counting a page of
results.

```json
{
  "documents": 412,
  "chunks": 8137,
  "embedded_chunks": 8137,
  "by_status": { "pending": 0, "processing": 0, "indexed": 409, "failed": 2, "unsupported": 1 }
}
```

`embedded_chunks` below `chunks` means some passages are stored but not yet
searchable; `scripts/backfill_embeddings.py` finishes them.

### `GET /documents/{id}`

Return one document by UUID, including `error_message` when ingestion failed. Returns `200`, `404` if unknown, or `422` for a malformed UUID.

### `DELETE /documents/{id}`

Delete a document and, by cascade, all of its chunks. Returns `204`, or `404` if unknown.

### `POST /chat`

Answer a question using only your indexed documents. Stateless — no conversation history is kept.

```bash
curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"question": "How does holiday accrue?", "top_k": 5}'
```

| Field | Type | Notes |
|---|---|---|
| `question` | string, 1–4000 chars | Required; must not be blank |
| `top_k` | integer, 1–50 | Optional; defaults to `RETRIEVAL_TOP_K` |

```json
{
  "answer": "Holiday accrues at two days per month, pro-rated for part-time staff.",
  "sources": [
    {
      "document": "handbook.pdf",
      "section": "Leave and Absence",
      "page": 12,
      "similarity": 0.91,
      "document_id": "9f1c2b6e-...",
      "chunk_id": "3a77e410-..."
    }
  ],
  "retrieved_chunks": 3
}
```

`page` carries whichever ordinal the format has — a page for PDF, a slide number for PPTX. DOCX has neither without rendering the file, so it is `null` there and `section` names the heading instead.

`sources` are the passages actually put in front of the model, so they cannot be invented — the model has no way to name a document that was not retrieved. A passage dropped because the context budget (`CHAT_CONTEXT_MAX_CHARS`) was reached is not listed either, since the model never saw it. The trade-off is that every passage that *was* shown is listed, including any the answer did not draw on.

When nothing relevant is found the endpoint returns `200` with `retrieved_chunks: 0`, an empty `sources`, and an answer saying so — and no model call is made. Check `retrieved_chunks`, not the prose, to tell that apart from a grounded answer.

| Status | When |
|---|---|
| `200` | Answered, or nothing relevant found |
| `422` | Blank question, or `top_k` out of range |
| `502` | The embedding or language model provider failed |

### `POST /search`

Run retrieval on its own and see what comes back. No prompt is rendered and no completion is requested, so this is what `POST /chat` would have been shown for the same query — without the latency, the cost, or the model's account of it. It is the endpoint to tune `RETRIEVAL_SIMILARITY_THRESHOLD` against.

```bash
curl -X POST http://localhost:8000/search \
     -H "Content-Type: application/json" \
     -d '{"question": "How does holiday accrue?", "top_k": 5}'
```

| Field | Type | Notes |
|---|---|---|
| `question` | string, 1–4000 chars | Required; must not be blank |
| `top_k` | integer, 1–50 | Optional; defaults to `RETRIEVAL_TOP_K` |

```json
{
  "query": "How does holiday accrue?",
  "results": [
    {
      "document": "handbook.pdf",
      "section": "Leave and Absence",
      "page": 12,
      "similarity": 0.91,
      "content": "Holiday accrues at two days per month...",
      "document_id": "9f1c2b6e-...",
      "chunk_id": "3a77e410-..."
    }
  ],
  "retrieved_count": 3
}
```

`results` are the passages `/chat` would draw on, in the same order and with the same `document`, `section` and `page` provenance — plus `content`, the passage itself, because a similarity score cannot explain itself without the text it scores. The context budget does not apply here: `/chat` may drop the least relevant of these before the model sees them, and `CHAT_CONTEXT_MAX_CHARS` is what governs that.

Nothing found is `200` with an empty `results` and `retrieved_count: 0`, never `404` — an empty knowledge base is a normal state.

| Status | When |
|---|---|
| `200` | Passages found, or nothing relevant |
| `422` | Blank query, or `top_k` out of range |
| `502` | The embedding provider failed |

### `GET /profile`, `PUT /profile`

Who the Shadow answers for. One profile; `404` until it is written.

```bash
curl -X PUT http://localhost:8000/profile \
     -H "Content-Type: application/json" \
     -d '{"name": "Test Executive", "role": "CEO", "organization": "SunRadia",
          "communication_style": "Concise and executive-friendly",
          "priorities": ["Government opportunities", "Enterprise AI"]}'
```

A `PUT` updates the fields it carries and leaves the rest alone, so correcting one does not mean resending the profile. `name`, `role` and `organization` are required on the first write and return `422` if absent.

### `GET /memory`, `POST /memory`, `PATCH /memory/{id}`, `DELETE /memory/{id}`

Durable things worth remembering. Types: `fact`, `preference`, `decision`, `commitment`, `context`. Importance runs 1–5.

```bash
curl -X POST http://localhost:8000/memory \
     -H "Content-Type: application/json" \
     -d '{"type": "decision", "content": "Prioritize government-sector opportunities.", "importance": 5}'
```

`GET /memory` takes `type` and `active` filters and returns everything most important first, retired memories included — it is the management view. `PATCH` with `{"active": false}` retires a memory, which is usually what is wanted: a decision that no longer applies is still a thing that was decided. `DELETE` is for the memory that should never have been stored.

Nothing writes memories except this endpoint. There is no extraction from chat and none from ingested documents.

### How the Digital Twin reaches an answer

`POST /chat` loads the profile and the most important active memories and places them above the retrieved passages:

```
[DIGITAL TWIN PROFILE]
Role: CEO
Priorities: Government opportunities; Enterprise AI
Communication style: Concise and executive-friendly

[MEMORY]
[DECISION] Prioritize government-sector opportunities.

[KNOWLEDGE SOURCES]
[SOURCE 1]
Document: capabilities.pdf
...
```

The profile and the memories decide tone, emphasis and which options are worth raising. They are never evidence: only the numbered passages can be cited as `[SOURCE n]`, and `sources` in the response is still built from retrieved chunks alone. The persona block is capped by `PERSONA_CONTEXT_MAX_CHARS` and comes *out of* `CHAT_CONTEXT_MAX_CHARS`, so adding a Digital Twin cannot make the prompt bigger than it already was. With no profile and no memories, the context is exactly what it was before the feature existed.

### `POST /sync/onedrive`

Synchronise configured OneDrive folders into the knowledge base. Incremental by
default; `full` re-enumerates a folder, which is safe to repeat because
unchanged content is skipped rather than re-indexed.

```bash
curl -X POST localhost:8000/sync/onedrive \
     -H 'Content-Type: application/json' \
     -d '{"source": "capabilities", "full": true}'
```

```json
{
  "sources": [
    {
      "source_key": "capabilities",
      "label": "Capabilities",
      "mode": "incremental",
      "status": "succeeded",
      "discovered": 12, "indexed": 3, "replaced": 1, "unchanged": 7,
      "deleted": 1, "unsupported": 0, "failed": 0,
      "duration_seconds": 41.2,
      "delta_advanced": true,
      "files": [
        {"name": "Capability Statement.pdf",
         "source_uri": "onedrive:b!abc:01XYZ",
         "result": "indexed",
         "document_id": "…", "reason": null}
      ]
    }
  ],
  "total_discovered": 12, "total_indexed": 3, "total_replaced": 1,
  "total_unchanged": 7, "total_deleted": 1, "total_unsupported": 0,
  "total_failed": 0, "duration_seconds": 41.2
}
```

`result` per file is `indexed`, `unchanged`, `replaced`, `deleted`,
`unsupported` or `failed`. `409` means nothing is configured; `404` an unknown
`source`; `502` that Graph refused or could not be reached.

`delta_advanced: false` means a transient failure kept the previous delta
token, so the next run re-examines the same window rather than skipping past a
file it never managed to download.

### `GET /sync/onedrive/status`

Stored sync state for every configured source. Contacts nothing. Never returns
the delta token itself — only `has_delta_token` — because the token is a bearer
credential for the window it describes.

Every configured source appears, including ones that have never run
(`never_run`) and ones switched off (`enabled: false`). A source with stored
state that is no longer in `ONEDRIVE_SOURCES` also appears, marked
`configured: false` — its documents are still in the knowledge base, so hiding
it would make them look like they came from nowhere.

```json
{
  "configured": true,
  "scheduled": true,
  "interval_seconds": 3600,
  "configuration_error": null,
  "sources": [
    {
      "source_key": "capabilities",
      "label": "Capabilities",
      "path": "Documents/Capabilities",
      "uri": null,
      "enabled": true,
      "configured": true,
      "drive_id": "b!…",
      "item_id": "01ABC…",
      "status": "succeeded",
      "has_delta_token": true,
      "error_message": null,
      "last_attempted_at": "2026-08-31T02:00:04Z",
      "last_succeeded_at": "2026-08-31T02:00:04Z",
      "last_duration_ms": 41230,
      "last_discovered": 96,
      "last_indexed": 3,
      "last_replaced": 1,
      "last_unchanged": 91,
      "last_deleted": 1,
      "last_unsupported": 0,
      "last_failed": 0
    }
  ]
}
```

`last_discovered` is derived from the counts beneath it rather than stored, so
it cannot disagree with them. `configuration_error` is set when
`ONEDRIVE_SOURCES` could not be parsed — "configured wrongly" and "not
configured" look identical from a status screen unless one of them says so.

Combine this with `GET /documents/stats` for a complete Knowledge Base view:
this endpoint owns the per-source facts, that one owns the corpus totals, and
neither restates the other.

### Email Agent

The Email Agent drafts as your Digital Twin, grounded in the shared knowledge
base when you ask for it. **It never sends anything on its own account.** A
draft leaves only after you approve it and then send it, and any edit withdraws
the approval — so what goes out is always the text you read.

Composing, revising, templates, drafts and triage of a message you supply all
work with **no mailbox connected**. Listing an inbox and sending do not. There
is no simulated mailbox anywhere in this system, so a draft showing `sent`
always means a real provider confirmed it.

All of these except `GET /email/provider/status` require `X-User-ID`. Templates,
drafts and assessments are private to that user; another user's is a `404`.

#### `GET /email/provider/status`

Whether a mailbox is connected. Never fails.

```json
{
  "provider": null,
  "configured": false,
  "connected": false,
  "mailbox": null,
  "detail": "No mailbox is connected. Drafting, rewriting, templates and saved drafts all work without one; listing an inbox and sending do not. Set EMAIL_PROVIDER and EMAIL_MAILBOX_ADDRESS on the server to connect one.",
  "capabilities": []
}
```

`configured` and `connected` are separate on purpose: nothing set up is a setup
step, and set-up-but-refused is a credentials or consent problem. They need
different fixes.

#### `POST /email/compose`

One endpoint for every generation and revision operation.

```bash
curl -X POST http://localhost:8000/email/compose \
  -H "X-User-ID: 7c9e6679-7425-40de-944b-e07fc1f90ae7" \
  -H "Content-Type: application/json" \
  -d '{
        "operation": "generate",
        "instruction": "Draft a proposal follow-up mentioning our analytics capabilities.",
        "recipients": ["ana@client.com"],
        "use_knowledge_base": true
      }'
```

```json
{
  "subject": "Following up on the analytics proposal",
  "body": "Ana,\n\nThank you for the time yesterday...",
  "operation": "generate",
  "sources": [
    {
      "document": "SunRadia Capabilities 2024.pdf",
      "section": "Analytics",
      "page": 3,
      "similarity": 0.81,
      "document_id": "…",
      "chunk_id": "…"
    }
  ],
  "knowledge_used": true,
  "persona_used": true
}
```

`operation` is one of `generate`, `reply`, `rewrite`, `improve`, `shorten`,
`expand`, `change_tone`, `professional`, `concise` or `subject`. Revisions take
the current `subject` and `body`; `reply` takes `source_subject`, `source_body`
and `source_sender`; `change_tone` takes `tone`.

`persona_used` is false when that user has no Digital Twin profile yet.
`knowledge_used` is false when `use_knowledge_base` was off **or when retrieval
found nothing** — and in that second case the model is explicitly told that no
company knowledge was retrieved and instructed to make no claim about SunRadia.
`sources` is built from retrieval, never from the model's output, so a source
cannot be fabricated.

**Nothing is saved and nothing is sent.** Turning this into a draft is a
separate call.

#### `GET/POST /email/templates`, `GET/PATCH/DELETE /email/templates/{id}`

User-owned templates. Placeholders are written `{{ name }}`, and the response
lists them under `placeholders`, derived from the text rather than stored.
Names are unique per owner — two people may both have a "Follow-up".

#### `POST /email/templates/{id}/fill`

Substitutes values and returns the result. **No model call**: this is textual
substitution. A placeholder you leave out stays visible as `{{ name }}` in the
output and is listed in `missing`, so you can see what you still owe rather
than finding a blank where a client's name should be.

#### `GET/POST /email/drafts`, `GET/PATCH/DELETE /email/drafts/{id}`

Drafts hold recipients, subject, body, attachments and provider identifiers.
Recipients are optional here and required at approval — a generated draft often
has none yet.

**`PATCH` withdraws approval**, always, whatever changed. A sent draft cannot be
edited at all; it can be deleted, which forgets this system's record and unsends
nothing.

#### `POST /email/drafts/{id}/attachments`, `DELETE …/{attachment_id}`

Multipart upload of one file. Any type — an attachment is whatever you mean to
send. Bounded by `EMAIL_MAX_ATTACHMENT_BYTES` and
`EMAIL_MAX_ATTACHMENTS_PER_DRAFT`. Attaching or detaching also withdraws
approval. Attachment bytes are never returned in a JSON response.

#### `POST /email/drafts/{id}/approve`

Records that you read this draft and want it sent. **It does not send.**
Requires recipients, a subject and a body.

#### `POST /email/drafts/{id}/send`

```bash
curl -X POST http://localhost:8000/email/drafts/$DRAFT_ID/send \
  -H "X-User-ID: $USER_ID" -H "Content-Type: application/json" \
  -d '{"confirm": true}'
```

Sends an already-approved draft. `409` when the draft is not approved, when it
has already been sent, or when no mailbox is connected. `502` when the provider
refused — the draft becomes `failed` with the reason in `send_error`, and its
approval is withdrawn, because the next attempt is a new decision.

#### `GET /email/messages`, `GET /email/messages/{id}`

Real messages from the connected mailbox, each with your assessment of it if it
has one. `409` when no mailbox is connected, rather than an empty list: "no
mail" and "no mailbox" are different facts.

#### `POST /email/triage`

Classifies one message as `urgent`, `needs_reply`, `fyi`, `follow_up` or
`low_priority`, with a separate priority, a summary, a suggested action, action
items and a follow-up recommendation — judged against your Digital Twin, so
what counts as urgent follows your responsibilities.

Send either `message_id`, to assess a message from the mailbox, or `message`, to
assess one you supply. The second is what makes triage usable before Outlook is
connected. `persist: false` assesses without storing, for a message that is in
no mailbox.

A `follow_up_due_at` appears only when the message itself gave a date. The
prompt forbids choosing one.

#### `POST /email/threads/summarize`

What was decided, what is open and who owes what. Takes `thread_id` or
`messages`. Not stored — a thread grows, and a saved summary of one is wrong as
soon as somebody replies.

#### `GET /email/assessments`, `GET /email/follow-ups`

Stored triage results, and the subset recommending a follow-up. Both read the
database only, so they work while the mailbox is unreachable. Follow-ups are
soonest-due first with undated ones last.

#### `POST /email/follow-ups/{id}/handled`

Marks a follow-up dealt with, or puts it back. Always yours to press — nothing
marks itself handled.

### Reports

Three reports share one envelope. `report_type` selects between them, and
`period` selects the window — omit it for the period in progress.

#### `GET /reports`

| Query | Values | Default | Meaning |
|---|---|---|---|
| `report_type` | `weekly_work` \| `weekly_email_digest` \| `monthly_email_digest` | `weekly_work` | Which report |
| `period` | `2026-08-31` (a Monday) or `2026-08` | the period in progress | Which window |
| `refresh` | boolean | `false` | Rebuild a period still running. No effect on a closed one |

```json
{
  "report_type": "weekly_email_digest",
  "status": "complete",
  "period": {
    "kind": "week",
    "key": "2026-08-24",
    "label": "24 Aug – 30 Aug 2026",
    "start": "2026-08-24T00:00:00Z",
    "end": "2026-08-31T00:00:00Z",
    "is_complete": true
  },
  "generated_at": "2026-08-31T01:00:00Z",
  "is_provisional": false,
  "from_history": true,
  "detail": null,
  "content": { "received_count": 42, "sent_count": 7, "untriaged_count": 30 }
}
```

`content` is the report body: the weekly-report shape for `weekly_work`, the
digest shape for the two digests, and `{}` when `status` is `unavailable`.

| Status | Meaning |
|---|---|
| `200` | The report, built now or read back from its snapshot |
| `400` | The period key is not one this system can name — a week key that is not a Monday, most often |
| `401` / `404` / `422` | The usual `X-User-ID` outcomes |

Two behaviours are worth knowing before reading a number off this endpoint. A
period that has **closed** is answered from its stored snapshot and is never
recomputed, so a past week does not move when today's work does; `refresh` will
not change that, by design. A digest with `status: "unavailable"` means no
mailbox was read — `detail` says why — and is **not** the same as a period in
which nothing arrived, which comes back `complete` with zeroes and
`is_quiet: true`.

#### `GET /reports/history`

Stored reports of one type, newest period first, plus `available_periods` —
what the calendar offers rather than what happens to be stored, so a period
nobody has generated can still be asked for.

#### `POST /reports/digests/run`

Snapshots the caller's last completed week and month of email now, the same way
the schedule does. Only ever runs for the caller. Idempotent: a period already
recorded costs a lookup and no mailbox call.

#### `GET /reports/weekly`

The live work report, unchanged. Built from the caller's current tasks and
meetings every time, with nothing stored — this is the endpoint the interactive
weekly screen uses.

### `GET /health`, `GET /`

Liveness probe and service information.

### Error format

Every mapped domain error returns the same shape:

```json
{ "detail": "Unsupported document type: application/zip. Supported formats are PDF, DOCX, TXT and Markdown.", "error": "UnsupportedDocumentTypeError" }
```

---

## Configuration

Set in `backend/.env`; see [`.env.example`](.env.example) for the full list with defaults.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local docker-compose Postgres | Connection string |
| `MAX_UPLOAD_SIZE_BYTES` | `10485760` (10 MiB) | Upload size limit |
| `CHUNK_SIZE` | `1000` | Target characters per chunk |
| `CHUNK_OVERLAP` | `150` | Characters shared between neighbouring chunks |
| `EMBEDDING_DIMENSIONS` | `1536` | Width of the embedding column |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Must return `EMBEDDING_DIMENSIONS`-wide vectors |
| `EMBEDDING_PROVIDER` | unset | Unset means "same as `LLM_PROVIDER`". Set to `openai` to route embeddings there only |
| `EMBEDDING_BATCH_SIZE` | `64` | Texts per provider call |
| `RETRIEVAL_TOP_K` | `5` | Chunks returned per search |
| `RETRIEVAL_SIMILARITY_THRESHOLD` | `0.0` | Cosine-similarity floor in `[-1, 1]`; leave empty to disable. Needs tuning on real documents |
| `MAX_MEMORIES_IN_CONTEXT` | `8` | Active memories that may shape one answer |
| `PERSONA_CONTEXT_MAX_CHARS` | `2000` | Ceiling on the profile and memory block, taken out of `CHAT_CONTEXT_MAX_CHARS` |
| `LLM_PROVIDER`, `LLM_MODEL` | `openrouter`, `openai/gpt-oss-20b` | Completions only; not used by ingestion |
| `REPORT_DIGEST_SCHEDULE_ENABLED` | `false` | Snapshot each user's last completed week and month of email on a timer |
| `REPORT_DIGEST_INTERVAL_SECONDS` | `3600` | How often that job *checks*. A period already recorded costs one query and no mailbox call |
| `BOOTSTRAP_ADMIN_EMAIL` | unset | Who becomes administrator when nobody is one. Unset promotes the earliest-created user |
| `EMAIL_INBOX_PAGE_SIZE` | `50` | Messages in one inbox page |
| `EMAIL_INBOX_MAX_PAGE_SIZE` | `200` | The most one request may ask for, so a page cannot become a crawl |

Every setting has a working default, so the application and its tests import without a `.env` present.

---

## Setting up OneDrive synchronisation

Nothing below is required to run the application; a deployment with no
credentials serves normally and reports `configured: false`.

**1. Register an application** in Microsoft Entra ID (Azure portal → App
registrations → New registration). Note the *Application (client) ID* and
*Directory (tenant) ID*.

**2. Grant application permissions.** Under API permissions, add Microsoft
Graph → **Application** permissions (not Delegated) → `Files.Read.All`. Then
**Grant admin consent** — without it every Graph call returns `403`, and the
error this application raises will say so.

`Files.Read.All` is the whole requirement for reading files. As an
*application* permission it is defined as "read files in all site collections",
which includes SharePoint document libraries and every user's OneDrive for
Business. `Sites.Read.All` is a different permission governing the `/sites`
discovery endpoints, and this application does not call them — granting it does
not widen file access and will not fix a `403` on a drive.

**3. Create a client secret** and copy the value immediately; it is shown once.

**4. Find the drive id.** For a person's OneDrive,
`GET /users/{user-principal-name}/drive`; for a SharePoint library,
`GET /sites/{site-id}/drives`. Or ask the application to do it, which needs no
Graph Explorer session:

```bash
cd backend
python -m scripts.discover_onedrive_sources --user someone@example.com
python -m scripts.discover_onedrive_sources --drive <drive-id>          # top-level folders
```

**5. Fill in `.env`** — `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`,
`ONEDRIVE_CLIENT_SECRET`, `ONEDRIVE_DRIVE_ID`. The secret is never logged, never
returned by any endpoint, and must never be committed.

**6. Configure the folders.** `ONEDRIVE_SOURCES` is a JSON array on one line,
and it is the *only* place a folder is named — nothing in application code
knows about any particular folder, so a sixth source is this line and a
restart.

```
ONEDRIVE_SOURCES=[{"key":"capabilities","label":"Capabilities","path":"Documents/Capabilities"},{"key":"case-study","label":"Case Study","path":"Documents/Capabilities/2024 and Earlier/Case Study"}]
```

| Field | Required | Notes |
|---|---|---|
| `key` | yes | Stable identity. Sync state and the delta token are stored against it, so renaming one forces a full resync. Duplicates are rejected. |
| `label` | no | What a person sees. Defaults to the path. |
| `drive_id` | no | Falls back to `ONEDRIVE_DRIVE_ID`. |
| `path` | one of | Folder path from the drive root. |
| `item_id` | one of | The folder's Graph id. Preferred once known. |
| `uri` | no | Human-facing link, shown on the status screen. Never used to address Graph. |
| `share_url` | one of | A sharing link, resolved by Graph. The route for a folder whose drive nobody knows. |
| `enabled` | no | `false` pauses a source without deleting it. Its delta token survives, so re-enabling resumes rather than re-indexing. Must be a JSON boolean. |

### Folders that arrived as a sharing link

A folder shared from another site, or from somebody else's OneDrive, has a
drive id nobody has written down — so there is no path to configure. Configure
the link instead:

```
{"key":"capabilities","label":"Capabilities","share_url":"https://…"}
```

Graph resolves it through `/shares/{token}/driveItem`, where the token is the
**whole URL** base64url-encoded. That is the opposite of decoding the id that
appears inside a sharing URL: the link is passed through unexamined, and the
`drive_id` and `item_id` are read out of Graph's reply. Resolution runs on the
sync path too, so a configured link needs no separate pinning step before its
first run.

A shortened `1drv.ms` link is **followed first**, because `/shares` is told a
URL and a short link is a redirect rather than the URL of anything. Following
it also settles the question that decides everything else — which service holds
the content:

| Destination host | What it is | Reachable app-only? |
|---|---|---|
| `tenant.sharepoint.com` | SharePoint document library | Yes, with `Files.Read.All` |
| `tenant-my.sharepoint.com` | A person's OneDrive for Business | Yes, with `Files.Read.All` |
| `onedrive.live.com` | A **consumer** Microsoft account | **No — and no permission can change that** |

An application token is issued by a tenant and has authority only inside it. A
consumer OneDrive belongs to a personal Microsoft account that is in no tenant,
so no consent granted to this application reaches it; the resolver reports that
as `outside_tenant` without asking Graph, because the `403` Graph would return
is indistinguishable from a missing permission and sends people granting
permissions that cannot help. `access_denied` (in-tenant, not consented) and
`not_found` (a wrong path) are reported as separate verdicts for the same
reason.

A **shortcut** to a shared folder — what "Add shortcut to My files" leaves in
somebody's own drive — is followed to its target automatically through the
item's `remoteItem`. The stub has no children and no delta of its own, so a
sync pointed at the stub's id would find an empty folder and report success.

For the SunRadia deployment the five folders are, on one line:

```
ONEDRIVE_SOURCES=[{"key":"cftc-dq-da","label":"CFTC / DQ-DA","path":"Documents/CFTC/DQ-DA/Final Submission Apr 29th 2024"},{"key":"amtrack-aws-migration","label":"Amtrack / AWS Migration","path":"Documents/Amtrack/AWS - Migration/Final/Submission folder"},{"key":"case-study","label":"Case Study","path":"Documents/Capabilities/2024 and Earlier/Case Study"},{"key":"freddie-mac-2026","label":"Freddie Mac 2026","path":"Documents/Freddie Mac 2026"},{"key":"capabilities","label":"Capabilities","path":"Documents/Capabilities"}]
```

Five entries, not six: the Amtrack folder was listed twice in the original
brief, and two entries for one folder would either collide on their key — which
`load_sources` refuses — or race each other for the same documents under two
delta tokens. Note also that `case-study` sits inside `capabilities`; that is
allowed and costs nothing, because a file already indexed under its own
`onedrive:{drive_id}:{item_id}` identity is `unchanged` on the second pass
rather than a duplicate.

**7. Diagnose, if anything is not where it was expected.** One command
resolves every source, says exactly why each one failed, and — for a known
drive — lists its root and searches it for each configured folder name, which
is what separates "the path is wrong" from "the folder is one level deeper":

```bash
cd backend
python -m scripts.discover_onedrive_sources --diagnose
```

**8. Pin the folders to ids.** A path is correct only until somebody renames a
parent folder; an id survives that. Ask Graph where each configured folder is
and paste the result back:

```bash
cd backend
python -m scripts.discover_onedrive_sources --resolve
```

It prints one block per source and then a complete `ONEDRIVE_SOURCES` line with
`drive_id` and `item_id` filled in. Sources it could not resolve are reported
with the reason and left out of that line, and the command exits non-zero — so
it also works as a deployment check. **No id is ever derived from a sharing
URL.** Every id it prints came from Graph answering a question about a path,
which is the difference between the right folder and a folder that looks right.

Resolution is optional: `POST /sync/onedrive` resolves a path on first contact
and stores the ids it got. Doing it ahead of time means a misspelled folder is
found at configuration time rather than at three in the morning.

**9. Run the first synchronisation.** It is a full enumeration: every supported
file is downloaded, ingested and embedded, so expect it to take a while and to
cost embedding calls.

```bash
curl -X POST localhost:8000/sync/onedrive -d '{}' -H 'Content-Type: application/json'
curl localhost:8000/sync/onedrive/status
```

Start with one folder — `-d '{"source":"capabilities"}'` — to check the
credentials and the path before pointing it at the whole corpus. Re-running is
safe: a file whose content has not changed is skipped, not re-embedded.

**10. Optionally enable the periodic run** with `ONEDRIVE_SYNC_ENABLED=true` and
`ONEDRIVE_SYNC_INTERVAL_SECONDS`. It runs incrementally, asking Graph only for
what changed. Read the single-worker caveat in
[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) first.

### What synchronisation does

| In OneDrive | In the knowledge base |
|---|---|
| A new file | Downloaded, parsed, chunked, embedded, indexed |
| A file whose content changed | The old document's chunks are replaced; the old text stops being retrievable |
| A file edited only in its metadata | Nothing. Content identity is Graph's `cTag`, so a description edit costs no embeddings |
| A file renamed or moved | Re-indexed in place. Identity is `onedrive:{drive_id}:{item_id}`, never the name or path |
| A deleted file | Its document and chunks are removed, so it stops appearing in answers |
| An unsupported format | Reported as `unsupported` with a reason. Never silently dropped |
| A file above the size limit | Reported as `unsupported` and not downloaded |
| A file that failed to download | Reported as `failed`; the run is `partial` and **the delta token does not advance**, so the next run tries again |

Supported formats are PDF, DOCX, PPTX, TXT and Markdown — the same list manual
upload accepts, because both paths call `ingestion_service.ingest_file`. There
is no second ingestion pipeline and no OneDrive-specific retriever: a synced
document and an uploaded one are indistinguishable to search, chat and
citation.

**How incremental sync works.** Graph's delta API returns only what changed
since an opaque token. The token is stored per source and is advanced **only
after** every file in the window has been dealt with. A transient failure — a
download that did not complete, Graph returning a 500 — holds it back, because
a token is a promise that everything before it has been seen, and a false
promise would bury that file until it happened to change again. If Graph
refuses a token as too old (`410 resyncRequired`), the run falls back to a full
enumeration automatically; that is idempotent, so the cost is time rather than
duplicated documents.

Throttling is Graph telling us the rate, not a failure: `429`, `503` and `504`
are retried with `Retry-After` honoured, up to a ceiling. One source failing
never stops the others, and two runs over the same source cannot overlap — the
claim is made in the database, so a scheduled run and a manual one cannot race.

### What still needs a tenant administrator

Nothing in this repository can grant itself access. A deployment needs, from
somebody with the rights to give it: an Entra application, the **application**
permission `Files.Read.All` (plus `Sites.Read.All` for SharePoint libraries),
**admin consent** for those permissions, and a client secret. Until consent is
granted every Graph call returns `403`, and the error this application raises
says exactly that rather than presenting it as a bug.

## Project structure

```
backend/app/
├── api/            FastAPI routes
├── config/         Pydantic settings
├── core/           exception hierarchy, shared constants
├── database/       engine, session factory, dependency, vector distance
├── models/         SQLAlchemy models
├── prompts/        prompt templates, registry, builder
├── schemas/        Pydantic request/response models
└── services/
    ├── email/      mailbox provider boundary
    │   └── provider/   EmailProvider protocol, Outlook, registry
    ├── engines/    reusable, domain-agnostic AI capabilities
    ├── features/   product features (documents/, retrieval/, chat/,
    │               digital_twin/, sync/, users/, email/)
    ├── graph/      Microsoft Graph client and drive service
    └── llm/        provider abstraction and embeddings

backend/scripts/    operational entrypoints (verification, backfill,
                    source discovery, offline static checks)
```

---

## Security

There is no authentication yet, and all data belongs to a single placeholder owner (`MVP_USER_ID`). Every table carries a `user_id` column and every query filters on it from the first migration, so introducing real authentication is a change to where that value comes from rather than a schema migration and a backfill.

The Email Agent adds one rule to that: **it cannot send an email by itself.**
Generation produces text, saving produces a draft, and a draft leaves only after
an explicit approval followed by an explicit send. Editing a draft withdraws its
approval, so an approved draft is always the text somebody read. Mail
permissions are also tenant-wide and the mailbox is a single configured address
— see [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).

Do not put production data in this system until authentication exists. See [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).

---

## Documentation

| Document | Contents |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | How work gets done here: process, standards, patterns |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design |
| [`docs/FEATURES.md`](docs/FEATURES.md) | What exists today — authoritative |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phases and next milestone |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why the codebase is shaped this way |
| [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) | Current gaps and limitations |
