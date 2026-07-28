# AI Shadow MVP

Upload documents, index them, and — once retrieval lands — chat with them and get answers with citations.

This repository is the focused MVP build. It reuses the components from the original `ai-shadow` prototype that earn their place (LLM provider abstraction, prompt system, Analysis Engine, configuration and testing patterns) and leaves behind the parts that were designed but not needed: the orchestrator, memory system, research and calendar tools, and a set of empty service stubs.

**Currently implemented: document upload and ingestion.** See [`docs/ROADMAP.md`](docs/ROADMAP.md) for what comes next.

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

# 5. Run
uvicorn app.main:app --reload
```

Interactive API documentation is served at `http://localhost:8000/docs`.

Run the tests with `pytest` from the `backend/` directory. The suite uses an in-memory SQLite database and needs no running services or API credentials.

---

## What ingestion does

```
Upload  →  Validate  →  Extract text  →  Chunk  →  Persist
           size            PDF: per page   configurable   documents
           type            DOCX: headings  size and       document_chunks
           emptiness       MD: headings    overlap        (embedding column
                           TXT: whole                      left null)
```

Chunks are stored with the page number and section heading they came from, which is what makes a citation resolvable back to a specific place in a specific document later.

The `document_chunks.embedding` column already exists as a nullable `vector(1536)`, with its HNSW index in place. The embedding feature fills it in; no migration is needed at that point.

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

A file that passes validation but fails to parse is stored with `status: "failed"` and an `error_message`, and returns `422`. A file rejected by validation is not stored at all.

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
| `LLM_PROVIDER`, `LLM_MODEL` | `openrouter`, `openai/gpt-oss-20b` | Not used by ingestion |

Every setting has a working default, so the application and its tests import without a `.env` present.

---

## Project structure

```
backend/app/
├── api/            FastAPI routes
├── config/         Pydantic settings
├── core/           exception hierarchy, shared constants
├── database/       engine, session factory, FastAPI dependency
├── models/         SQLAlchemy models
├── prompts/        prompt templates, registry, builder
├── schemas/        Pydantic request/response models
└── services/
    ├── engines/    reusable, domain-agnostic AI capabilities
    ├── features/   product features (documents/)
    └── llm/        provider abstraction
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
