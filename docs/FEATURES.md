# AI Shadow MVP — Features Catalog

The authoritative answer to "does X exist today". Where this document and [`ARCHITECTURE.md`](ARCHITECTURE.md) disagree, this document wins. For phase sequencing see [`ROADMAP.md`](ROADMAP.md).

---

## Implemented

### Document Upload & Ingestion
Upload a PDF, DOCX, TXT or Markdown file; it is validated, its text extracted with page and heading provenance, split into overlapping chunks, embedded, and persisted. `POST /documents/upload`. A document reaches `indexed` only once every chunk carries a vector.
- **Status:** Implemented
- **Dependencies:** Embedding Generation, Document Models & Migrations, Configuration Management

### Embedding Generation
Chunk text is embedded through the configured provider and stored in `document_chunks.embedding`. Requests are batched, vectors are matched to chunks by the provider's index, and a width that disagrees with the column is rejected before storage. The embedding provider can be configured independently of the completion provider.
- **Status:** Implemented
- **Dependencies:** LLM Provider Abstraction, Document Models & Migrations

### Embedding Backfill
Embeds chunks that have no vector — documents ingested before embeddings existed, and documents whose embedding step failed at upload. Idempotent and resumable, committed per document. `python -m scripts.backfill_embeddings`.
- **Status:** Implemented
- **Dependencies:** Embedding Generation

### Embedding Provider Verification
A one-shot live call reporting the configured provider, model and returned width, so a misconfiguration is caught before any document is ingested. `python -m scripts.verify_embedding_provider`.
- **Status:** Implemented
- **Dependencies:** Embedding Generation

### Semantic Retrieval
Top-k cosine similarity search over stored chunk vectors, run in the database against the HNSW index. Configurable `top_k` and similarity floor, scoped by user, restricted to `indexed` documents, with duplicate content collapsed on a whitespace- and case-insensitive fingerprint, so the same passage under two filenames does not take two of the slots a caller asked for. Results carry document, filename, page and heading, so a citation can be resolved. Reached over HTTP through `POST /chat`.
- **Status:** Implemented
- **Dependencies:** Embedding Generation, Document Models & Migrations

### Dialect-Aware Vector Distance
A `cosine_distance` construct compiling to pgvector's `<=>` on Postgres and to a registered function on SQLite, so the retrieval query under test is the query that ships.
- **Status:** Implemented
- **Dependencies:** Document Models & Migrations

### RAG Chat with Citations
`POST /chat` answers a question using only the caller's indexed documents. Retrieval supplies the context, which is assembled into delimited `[SOURCE n]` blocks naming the document, section and page of each passage; the registered `rag_answer` prompt constrains the model to those passages and instructs it to cite them by identifier. The response carries each passage as `document`, `section`, `page` and `similarity`. Context is bounded by `CHAT_CONTEXT_MAX_CHARS` — passages dropped for budget are not reported as sources, because the model never saw them. Stateless: no conversation history is kept or consulted.
- **Status:** Implemented
- **Dependencies:** Semantic Retrieval, Prompt Registry System, LLM Provider Abstraction

### Document Management API
List documents with status filtering and pagination, retrieve one by id including its failure reason, and delete a document with its chunks. `GET /documents`, `GET /documents/{id}`, `DELETE /documents/{id}`.
- **Status:** Implemented
- **Dependencies:** Document Models & Migrations

### Document Models & Migrations
`documents` and `document_chunks` tables via SQLAlchemy models and an Alembic baseline migration, including the pgvector extension, a nullable `vector(1536)` embedding column, and its HNSW index.
- **Status:** Implemented
- **Dependencies:** Database Connectivity Layer

### Domain Error Mapping
Every document error maps to a specific status code (`404`, `413`, `415`, `422`) with a consistent JSON body. LLM errors map to `502`. Routes and services never construct `HTTPException` directly.
- **Status:** Implemented
- **Dependencies:** None

### Configuration Management
Environment-driven settings via Pydantic Settings, with a working default for every value so the application imports without a populated `.env`.
- **Status:** Implemented
- **Dependencies:** None

### Database Connectivity Layer
SQLAlchemy engine, session factory, and a FastAPI session dependency. SQL echo follows `DEBUG`.
- **Status:** Implemented
- **Dependencies:** None

### LLM Provider Abstraction
Provider-agnostic chat completions and embeddings across OpenAI and OpenRouter, selected by configuration and cached per provider. Clients are constructed lazily on first use, so importing the package requires no credentials.
- **Status:** Implemented — both paths now have callers
- **Dependencies:** None

### Prompt Registry System
`PromptTemplate`, `PromptRegistry` and `PromptBuilder`, with two built-in prompts: `rag_answer`, used by chat, and `assistant`, which still has no caller.
- **Status:** Implemented
- **Dependencies:** None

### Analysis Engine
Runs a registered prompt, calls the LLM, tolerates markdown-fenced JSON, and validates the result against a caller-supplied Pydantic model.
- **Status:** Implemented — **no caller yet**
- **Dependencies:** Prompt Registry System, LLM Provider Abstraction

### Health & Root Endpoints
`GET /health`, `GET /`.
- **Status:** Implemented

> **On the remaining "no caller" entry.** Only the Analysis Engine is left unused. Chat derives its sources from retrieval rather than from validated model output, so nothing currently needs schema-checked JSON — see the RAG Chat decision in `DECISIONS.md`. It earns its place if and when citations move to sentence level; otherwise it should be removed.

---

## In Progress

None. See [`ROADMAP.md`](ROADMAP.md).

---

## Planned

### Sentence-Level Citations
Attribution of individual claims to specific passages, by having the model cite by index and validating those indices through the Analysis Engine. Today every retrieved passage is returned as a source, including any the model did not use.
- **Status:** Planned
- **Dependencies:** RAG Chat, Analysis Engine

### Search Endpoint
`POST /search` exposing retrieval without the model, for debugging relevance.
- **Status:** Planned
- **Dependencies:** Semantic Retrieval

### Frontend
React and Tailwind interface for upload, document management, chat, and source display.
- **Status:** Planned
- **Dependencies:** RAG Chat with Citations

### Authentication & Multi-User Support
User accounts and per-user data isolation. Every table already carries `user_id` and every query already filters on it.
- **Status:** Planned
- **Dependencies:** Document Models & Migrations

### Background Ingestion
Move ingestion off the request thread once documents are large enough for synchronous processing to be a problem.
- **Status:** Planned
- **Dependencies:** Document Upload & Ingestion

---

## Explicitly out of scope for the MVP

Carried over from the reference repository's design but deliberately not built here: the AI Orchestrator, conversation/user/task memory, the tool architecture (email, calendar, research, search), document generation, and multi-agent workflows.
