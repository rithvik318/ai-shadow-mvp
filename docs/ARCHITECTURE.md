# AI Shadow MVP — Architecture

**Scope note.** This document describes the system being built, and stays close to what exists. Where it describes something not yet implemented it is marked *(planned)*. [`FEATURES.md`](FEATURES.md) is the authoritative record of what exists today; where the two disagree, `FEATURES.md` wins.

---

## 1. What this system does

Users upload documents. The documents are indexed. Users ask questions and get answers grounded in those documents, with citations back to a specific page or section.

That sentence is the whole product for now. The reference `ai-shadow` repository describes a considerably larger system — an AI Orchestrator mediating memory, knowledge and a six-tool action layer. None of it is needed here, and none of it is built. See [`DECISIONS.md`](DECISIONS.md).

---

## 2. Layers

```
HTTP  ──▶  API layer            app/api/
                                routes, request/response schemas
             │
             ▼
           Feature services     app/services/features/
                                product logic: documents/, retrieval/, chat/
             │
             ├──────────────▶   Engines            app/services/engines/
             │                  reusable, domain-agnostic AI capabilities
             │                    │
             │                    ▼
             │                  LLM layer          app/services/llm/
             │                  the only place that knows an LLM vendor
             │
             ├──────────────▶   External providers  app/services/graph/
             │                                      app/services/email/provider/
             │                  the only places that know a SaaS vendor
             ▼
           Persistence          app/models/, app/database/
                                SQLAlchemy models, engine, session
```

Dependencies point strictly inward. A feature service may use an engine; an engine may use the LLM layer; nothing lower reaches back up. Configuration (`app/config/`), the exception hierarchy (`app/core/`) and prompt templates (`app/prompts/`) are leaves that any layer may import.

There are three vendor boundaries and they are the same idea applied three times. `app/services/llm/client.py` is the only module that names an LLM provider. `app/services/graph/` is the only package that knows what a bearer token or a drive id is. `app/services/email/provider/` is the only package that knows what Outlook is — everything above it speaks in the provider-neutral dataclasses declared in `base.py`, and nothing inside it imports a model, a schema or a service. Adding Gmail is a module beside `outlook_provider.py` and one entry in the registry.

---

## 3. Ingestion pipeline

Two entry points, one pipeline. `POST /documents/upload` raises, and its errors
become status codes; `POST /documents/batch-upload` reports the same work as a
value per file, because a batch cannot use exceptions — the first bad file would
end the run and the good files after it would never be attempted. Both call
`ingestion_service`, and the later OneDrive synchronisation will call the same
function with a `source_uri`. A second caller must not mean a second pipeline.

```
validate ──▶ identify ──▶ parse ──▶ chunk ──▶ embed ──▶ indexed
   │             │           │                  │
   │             │           └── failed         └── failed (chunks kept
   │             │                                   for the backfill)
   │             └── unchanged: same bytes already indexed, nothing re-done
   │             └── replaced: known source, new bytes — old chunks dropped
   └── rejected before any row exists: empty, oversized, unsupported
```

Replacement drops the old chunks only *after* the new content has parsed and
chunked, so a re-index that cannot be read leaves the previous chunks in place
rather than emptying the document — and the `failed` status keeps them out of
retrieval either way.

```
POST /documents/upload
        │
        ▼
   validate_upload()          filename, emptiness, size, format
        │                     ─ failure: nothing persisted, 4xx returned
        ▼
   Document(status=processing) persisted
        │
        ▼
   parse_document()           bytes → sections with provenance
        │                     PDF   → one section per page, page_number set
        │                     DOCX  → one section per heading, section_title set
        │                     MD    → one section per ATX heading
        │                     TXT   → a single untitled section
        │                     ─ failure: status=failed + error_message, 422
        ▼
   chunk_document()           each section split independently, so a chunk
        │                     never spans two pages or two headings
        ▼
   DocumentChunk rows          content, char_count, chunk_index,
        │                      page_number, section_title, embedding=NULL
        ▼
   embed_document_chunks()    batched provider calls; vectors matched to
        │                     chunks by the provider's own index
        │                     ─ failure: status=failed, chunks KEPT, 502
        ▼
   Document(status=indexed, chunk_count, page_count)   → 201
```

The whole pipeline runs inside the request and inside one transaction.

`status="indexed"` means retrievable. A document whose chunks carry no vectors
is invisible to similarity search, so reporting it as indexed would make an
unretrievable document indistinguishable from an irrelevant question. Chunks
survive an embedding failure — unlike a parse failure, the provider outage is
transient and the parse work is still valid, so recovery is
`backfill_missing_embeddings` rather than a re-upload.

### Provenance

A chunk carries the page number and section heading its text came from. This is the reason the pipeline splits per section rather than concatenating the document first: without it, a citation can name a document but not a place in it, which is the difference between a source the user can check and one they have to take on faith.

---

## 3a. OneDrive synchronisation

```
Microsoft Graph
   │  client-credentials token, cached
   ▼
app/services/graph/          client.py       auth, paging, delta, downloads
   │                         drive_service.py  payloads → DriveItem
   ▼
app/services/features/sync/  source_config.py  ONEDRIVE_SOURCES → sources
   │                         onedrive_sync_service.py
   ▼
ingestion_service.ingest_file(source_uri=…, source_version=…)   §3
   ▼
the same documents and chunks every other upload path produces
```

Nothing below the sync service knows what a document is, and nothing above it
knows what a bearer token is — the split `app/services/llm/` draws around the
model provider, for the same reason.

**Identity is the Graph item.** `source_uri` is `onedrive:{drive_id}:{item_id}`
and `source_version` is the item's cTag. Neither is the filename or the path,
so a file that is renamed or moved re-indexes in place instead of arriving as
a second copy of itself. Everything else follows from §4's identity rules.

**The delta token is a promise.** `onedrive_sync_state.delta_link` means
"everything before this has been dealt with", so it is written only after the
work it describes is done. A file whose *download* failed was never seen, so
its run keeps the previous token and reports `partial`; a file that failed to
*parse* will fail identically forever, so it does not hold the token back.
That distinction is the difference between a sync that resumes and one that
either loses files or freezes.

**Deletion is Graph's to declare.** A delta item carrying a `deleted` facet
removes the document at that `source_uri`, and its chunks go by cascade.

---

## 4. Data model

```
documents                          document_chunks
─────────────────────────          ────────────────────────────────
id            uuid  PK             id             uuid  PK
user_id       str   idx  ────┐     document_id    uuid  FK → documents.id
filename      str            │                          ON DELETE CASCADE
content_type  str            │     user_id        str   idx  (denormalised)
file_size_    int            │     chunk_index    int
  bytes                      │     content        text
page_count    int?           │     char_count     int
chunk_count   int            │     page_number    int?
status        enum  idx      │     section_title  str?
error_message text?          │     embedding      vector(1536)?  ← nullable
created_at    tstz           │     created_at     tstz
updated_at    tstz           │
                             └──── UNIQUE (document_id, chunk_index)
                                   HNSW INDEX (embedding vector_cosine_ops)
```

`status` moves `pending → processing → indexed | failed`.

Two properties of this schema matter more than the rest. The **embedding column and its index were created up front**, which is why populating them needed no migration — the column is still nullable, and nullable now means "not yet embedded", which is exactly what the backfill looks for. And **every row is user-scoped** from the first migration, so introducing authentication changes where `user_id` comes from rather than requiring a backfill.

Migration `0004` added `content_hash`, `source_uri` and `source_version` to
`documents`. They are what makes ingestion idempotent: the hash answers "are
these the same bytes?", and the source answers "is this the same document,
changed?" — which the hash cannot, since the hash is what changed. `content_hash`
is nullable and NULL means "identity unknown", which never matches, so documents
predating the column are never wrongly deduplicated. `source_uri` is unique per
user where present, via a partial index.

Migration `0006` added four email tables — `email_template`, `email_draft`,
`email_attachment` and `email_assessment` — all owned by a `User` the way the
Digital Twin is. Two decisions there are worth restating because they are easy
to reverse by accident.

**There is no message table.** A mailbox is somebody else's system of record,
and a copy of it here would be a mirror that silently drifts. `email_assessment`
stores what triage *concluded* about a message, keyed by `provider` plus that
provider's own message id, together with the subject, sender and timestamp a
person needs to recognise the row. Bodies are not stored, and messages are read
from the provider on demand.

**Provider identifiers are plain nullable strings with a `provider` column
beside them**, never foreign keys and never parsed. An Outlook id and a Gmail
id are both strings, so a second provider is neither a migration nor a new
column — which is what keeps Microsoft Graph out of the schema entirely.

Every timestamp on those four tables is `UtcDateTime` rather than a plain
`DateTime(timezone=True)`. The difference is that the declaration is actually
enforced: Postgres honours `timezone=True`, SQLite silently ignores it, and
SQLAlchemy's SQLite bind processor formats a datetime from its clock fields
while discarding `tzinfo` — so an offset-bearing value would be stored as its
local wall clock and read back as UTC. The decorator converts to UTC before
binding and labels UTC on reading, which keeps the instant intact both ways and
means code above it never has to ask which database it is on. That is the same
bargain `app/database/vector.py` strikes for cosine distance.

`users`, `digital_twin_profile` and `digital_twin_memory` were added later (migrations `0002` and `0003`). The twin tables carry a UUID `user_id` foreign key into `users`; the document tables keep a plain string `user_id`, and it stays the shared `MVP_USER_ID`. That asymmetry is intentional: **the corpus is company-wide and the twin is personal.** See [`PROJECT_STATE.md`](PROJECT_STATE.md).

---

## 5. Retrieval

```
search(query, top_k, similarity_threshold)
   │
   ▼
reject a blank query                        → EmptyQueryError
   │
   ▼
probe for one eligible chunk                → [] before any provider call
   │
   ▼
EmbeddingService.embed_query(query)         → EmbeddingError propagates
   │
   ▼
ORDER BY embedding <=> :query_vector,       cosine distance, in the database
         id                                 against the HNSW index
   │  WHERE user_id = :user
   │    AND embedding IS NOT NULL
   │    AND documents.status = 'indexed'
   │    AND distance IS NOT NULL
   │    AND distance <= 1 - threshold       (omitted when the floor is off)
   ▼
page until top_k unique contents, or the corpus is exhausted
   ▼
RetrievedChunk[]  { content, similarity, document_id, filename,
                    chunk_index, page_number, section_title }
```

Ranking happens in the database. Loading vectors into the application to sort
them would ignore the HNSW index and turn every search into a full scan.

**The metric is cosine similarity**, reported to callers as `1 - distance` in
`[-1, 1]`. The HNSW index is built `USING hnsw (embedding vector_cosine_ops)`,
and an index only serves the operator class it was built for — so L2 (`<->`)
would still return correct answers while silently dropping to a sequential
scan. Cosine is also magnitude-invariant, which keeps a long chunk from
outranking a short one on norm alone, and its bounded range makes the
configurable floor a number a human can reason about. Changing the metric means
changing the index. See [`DECISIONS.md`](DECISIONS.md).

The floor is configurable and can be disabled — per call with
`similarity_threshold=None`, or globally by leaving
`RETRIEVAL_SIMILARITY_THRESHOLD` empty. Deduplication pages further down the
ranking rather than over-fetching a fixed multiple, so collapsing repeated
boilerplate never returns fewer chunks than were asked for.

`<=>` is a pgvector operator, and the suite runs on SQLite. Rather than write
two queries, `app/database/vector.py` defines one `cosine_distance` construct
that compiles to `<=>` on Postgres and to a registered function on SQLite — and
an opt-in test (`pytest -m postgres`) asserts the two agree. See
[`DECISIONS.md`](DECISIONS.md).

Retrieval knows nothing about prompts, chat or citations. It returns chunks and
their provenance; assembling those into an answer is the next layer's job.

---

## 6. Chat

```
POST /chat  { question, top_k? }
   │
   ▼
ChatRequest validation                      blank question → 422
   │
   ▼
retrieval_service.search(question, top_k)   §5
   │
   ├── no passages ──▶ 200, fixed "nothing found" answer,
   │                   retrieved_chunks: 0, model NOT called
   ▼
PromptBuilder.build(registry["rag_answer"],
                    context=numbered passages, question=...)
   │
   ▼
llm_service.complete(messages)              provider failure → 502
   │
   ▼
{ answer, sources[], retrieved_chunks }
```

`ChatService` is orchestration and nothing else: no similarity arithmetic, no
prompt text, no provider knowledge. Each of those already belongs to a layer
below it, and chat composes them.

**Sources are the retrieved passages**, not something the model reports. The
model cannot name a document that was not fetched, so a source cannot be
fabricated. The price is that attribution is per-request rather than
per-sentence — every retrieved passage is listed, including any the model did
not use. See [`DECISIONS.md`](DECISIONS.md).

The diagram above is the knowledge half. When the caller carries an `X-User-ID`,
that user's profile and active memories are rendered into a persona block and
placed *above* the numbered passages, under `[DIGITAL TWIN PROFILE]` and
`[MEMORY]`. It shapes tone, emphasis and priorities and is never citable — with
no profile and no memories the assembled context is byte-for-byte what it was
before the feature existed.

Stateless. No conversation history is stored or consulted; each question is
answered from the documents alone.

---

## 6a. Email Agent

```
POST /email/compose
   │
   ├──▶ profile_service + memory_service + persona_service   who is writing
   ├──▶ retrieval_service + context_service                  what is true
   │        (only when use_knowledge_base; otherwise the prompt
   │         explicitly states that nothing was retrieved)
   ▼
   analysis_engine.run(prompt, GeneratedEmail, **variables)
   │
   ▼
   {subject, body} + the passages retrieval actually supplied
```

Nothing above is new machinery. The composer is an orchestrator in exactly the
sense `chat_service` is: it asks four existing services for their pieces and
composes them. There is no email persona, no email retriever and no email
prompt mechanism.

The one genuinely new idea is the **operation**. Ten named transformations —
`generate`, `reply`, `rewrite`, `improve`, `shorten`, `expand`, `change_tone`,
`professional`, `concise`, `subject` — share three prompts, because they differ
by a sentence of instruction rather than by a mechanism. Ten endpoints that
differed by a sentence would be ten things to keep in step.

Every operation runs through the `AnalysisEngine` with a Pydantic response
model, which is what closes the "component without a caller" exception recorded
against it. That matters most for triage: a malformed reply to "write me an
email" is visible to whoever reads the draft, while a malformed reply to "is
this urgent" would become a label in a list nobody re-reads.

### The send path

```
generate ──▶ save ──▶ review ──▶ approve ──▶ send ──▶ sent | failed
                        ▲           │
                        └───────────┘
                    any edit withdraws approval
```

Three refusals hold this together, and each is a test rather than a convention:

1. `sending_service.send_draft` raises unless the draft is `approved`, and it
   checks that **before** resolving a provider — so an unapproved draft is
   refused for being unapproved even on a fully connected system.
2. Every mutation in `draft_service` funnels through `_touch`, which clears
   `approved_at` and returns the draft to `draft`. Approval therefore always
   means "send *this* text", never "send whatever is in this row at send time".
3. `sent` is written in one place, on a `SendReceipt` from a provider. Every
   failure path writes `failed` with a reason. There is no provider in the
   codebase that fabricates a receipt, so there is no path to a false `sent`.

Nothing in the LLM layer can reach a provider, and nothing in the provider
package can reach a service. The agent writes; a person approves; the sending
service sends.

---
## 7. Error handling

Services raise domain exceptions from `app/core/exceptions.py`. They know nothing about HTTP. Exception handlers registered in `app/main.py` map them:

| Exception | Status |
|---|---|
| `DocumentNotFoundError` | 404 |
| `DocumentTooLargeError` | 413 |
| `UnsupportedDocumentTypeError` | 415 |
| `EmptyDocumentError`, `DocumentParseError` | 422 |
| any other `DocumentError` | 400 |
| `RetrievalError`, `EmptyQueryError` | 422 |
| `AnalysisValidationError`, `LLMServiceError`, `EmbeddingError` | 502 |

Every mapped error returns `{"detail": "...", "error": "ExceptionClassName"}`.

---

## 8. Configuration

All settings come from environment variables through `app/config/settings.py`, and every one has a working default so the package imports without a `.env`. Nothing reads `os.environ` directly.

---

## 9. Testing

The test tree mirrors the application tree. Tests run against in-memory SQLite with no services and no credentials: the embedding column is declared with a JSON variant for SQLite, so the same models create cleanly in both dialects.

Parsing and chunking are tested as pure functions over generated fixture documents — a hand-built multi-page PDF, a DOCX with headings, Markdown and plain text — rather than through the ORM. Ingestion is tested against a real in-memory database including the cascade delete. The HTTP surface is tested through `TestClient`, covering each supported format, each rejection path and its status code, pagination, filtering and deletion.

---

## 10. Planned: sentence-level citations

*(Not implemented. The retrieval half of this now exists — see §5.)*

```
POST /chat
   │
   ▼
retrieval_service.search(question)  ────────▶ exists (§5)
   │
   ▼
top-k chunks with their page_number and section_title
   │
   ▼
AnalysisEngine.run("rag_answer", RagAnswer, context=..., input=...)
   │  numbered context blocks; the model cites by index
   ▼
RagAnswer { answer, citations[] }  ← validated, not parsed from prose
   │
   ▼
citation indices resolved back to document / page / heading
```

The Analysis Engine already does the hard part: it forces the model's output through a Pydantic schema and fails loudly otherwise. That turns "are the citations well-formed" from a parsing problem into a validation guarantee.

---

## 11. Technology

| Layer | Choice |
|---|---|
| API | FastAPI |
| Validation & settings | Pydantic, Pydantic Settings |
| Database | PostgreSQL with pgvector |
| ORM & migrations | SQLAlchemy 2.0, Alembic |
| Parsing | pypdf, python-docx |
| Chunking | langchain-text-splitters |
| LLM | OpenAI SDK against OpenAI or OpenRouter |
| Embeddings | Same SDK; provider selectable independently of completions |
| Testing | pytest, SQLite in-memory |
| Lint & format | ruff |
| Frontend *(planned)* | React, Tailwind |
