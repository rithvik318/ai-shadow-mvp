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
             │                  the only place that knows a provider name
             ▼
           Persistence          app/models/, app/database/
                                SQLAlchemy models, engine, session
```

Dependencies point strictly inward. A feature service may use an engine; an engine may use the LLM layer; nothing lower reaches back up. Configuration (`app/config/`), the exception hierarchy (`app/core/`) and prompt templates (`app/prompts/`) are leaves that any layer may import.

---

## 3. Ingestion pipeline

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

Stateless. No conversation history is stored or consulted; each question is
answered from the documents alone.

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
