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
| `LLM_PROVIDER`, `LLM_MODEL` | `openrouter`, `openai/gpt-oss-20b` | Completions only; not used by ingestion |

Every setting has a working default, so the application and its tests import without a `.env` present.

---

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
    ├── engines/    reusable, domain-agnostic AI capabilities
    ├── features/   product features (documents/, retrieval/)
    └── llm/        provider abstraction and embeddings

backend/scripts/    operational entrypoints (verification, backfill)
```

---

## Security

There is no authentication yet, and all data belongs to a single placeholder owner (`MVP_USER_ID`). Every table carries a `user_id` column and every query filters on it from the first migration, so introducing real authentication is a change to where that value comes from rather than a schema migration and a backfill.

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
