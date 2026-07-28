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
                                product logic: documents/
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
   Document(status=indexed, chunk_count, page_count)   → 201
```

The whole pipeline runs inside the request and inside one transaction.

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

Two properties of this schema matter more than the rest. The **embedding column and its index already exist**, nullable and unpopulated, so the embedding feature is a data change rather than a migration. And **every row is user-scoped** from the first migration, so introducing authentication changes where `user_id` comes from rather than requiring a backfill.

---

## 5. Error handling

Services raise domain exceptions from `app/core/exceptions.py`. They know nothing about HTTP. Exception handlers registered in `app/main.py` map them:

| Exception | Status |
|---|---|
| `DocumentNotFoundError` | 404 |
| `DocumentTooLargeError` | 413 |
| `UnsupportedDocumentTypeError` | 415 |
| `EmptyDocumentError`, `DocumentParseError` | 422 |
| any other `DocumentError` | 400 |
| `AnalysisValidationError`, `LLMServiceError` | 502 |

Every mapped error returns `{"detail": "...", "error": "ExceptionClassName"}`.

---

## 6. Configuration

All settings come from environment variables through `app/config/settings.py`, and every one has a working default so the package imports without a `.env`. Nothing reads `os.environ` directly.

---

## 7. Testing

The test tree mirrors the application tree. Tests run against in-memory SQLite with no services and no credentials: the embedding column is declared with a JSON variant for SQLite, so the same models create cleanly in both dialects.

Parsing and chunking are tested as pure functions over generated fixture documents — a hand-built multi-page PDF, a DOCX with headings, Markdown and plain text — rather than through the ORM. Ingestion is tested against a real in-memory database including the cascade delete. The HTTP surface is tested through `TestClient`, covering each supported format, each rejection path and its status code, pagination, filtering and deletion.

---

## 8. Planned: retrieval and cited chat

*(Not implemented. Sketched here because the schema above was shaped by it.)*

```
POST /chat
   │
   ▼
embed the question ─────────▶ embedding service (planned)
   │
   ▼
similarity search over document_chunks.embedding
   │  cosine distance, scoped by user_id, above a similarity floor
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

## 9. Technology

| Layer | Choice |
|---|---|
| API | FastAPI |
| Validation & settings | Pydantic, Pydantic Settings |
| Database | PostgreSQL with pgvector |
| ORM & migrations | SQLAlchemy 2.0, Alembic |
| Parsing | pypdf, python-docx |
| Chunking | langchain-text-splitters |
| LLM | OpenAI SDK against OpenAI or OpenRouter |
| Testing | pytest, SQLite in-memory |
| Lint & format | ruff |
| Frontend *(planned)* | React, Tailwind |
