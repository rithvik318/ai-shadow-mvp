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

Every setting has a working default, so the application and its tests import without a `.env` present.

---

## Setting up OneDrive synchronisation

Nothing below is required to run the application; a deployment with no
credentials serves normally and reports `configured: false`.

**1. Register an application** in Microsoft Entra ID (Azure portal → App
registrations → New registration). Note the *Application (client) ID* and
*Directory (tenant) ID*.

**2. Grant application permissions.** Under API permissions, add Microsoft
Graph → **Application** permissions (not Delegated) → `Files.Read.All`, plus
`Sites.Read.All` if the folders live in a SharePoint document library. Then
**Grant admin consent** — without it every Graph call returns `403`, and the
error this application raises will say so.

**3. Create a client secret** and copy the value immediately; it is shown once.

**4. Find the drive id.** For a person's OneDrive,
`GET /users/{user-principal-name}/drive`; for a SharePoint library,
`GET /sites/{site-id}/drives`. Graph Explorer is the easiest way to run these.

**5. Fill in `.env`** — `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`,
`ONEDRIVE_CLIENT_SECRET`, `ONEDRIVE_DRIVE_ID`.

**6. Configure the folders.** `ONEDRIVE_SOURCES` is a JSON array on one line.
Each entry needs a stable `key` and either a `path` or an `item_id`:

```
ONEDRIVE_SOURCES=[{"key":"capabilities","path":"Capabilities"},{"key":"case-studies","path":"Capabilities/2024 and Earlier/Case Study"}]
```

The `key` is what sync state is stored against, so changing one orphans its
delta token and forces a full resynchronisation. Prefer `item_id` once you know
it — `GET /sync/onedrive/status` reports the resolved id after the first run —
because a path stops being correct the moment somebody renames a parent folder.

**7. Run the first synchronisation.** It is a full enumeration: every supported
file is downloaded, ingested and embedded, so expect it to take a while and to
cost embedding calls.

```bash
curl -X POST localhost:8000/sync/onedrive -d '{}' -H 'Content-Type: application/json'
curl localhost:8000/sync/onedrive/status
```

Start with one folder to check the credentials and the path before pointing it
at the whole corpus.

**8. Optionally enable the periodic run** with `ONEDRIVE_SYNC_ENABLED=true` and
`ONEDRIVE_SYNC_INTERVAL_SECONDS`. It runs incrementally, asking Graph only for
what changed. Read the single-worker caveat in
[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) first.

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
