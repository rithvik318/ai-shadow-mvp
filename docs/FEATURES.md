# AI Shadow MVP — Features Catalog

The authoritative answer to "does X exist today". Where this document and [`ARCHITECTURE.md`](ARCHITECTURE.md) disagree, this document wins. For phase sequencing see [`ROADMAP.md`](ROADMAP.md).

---

## Implemented

### Document Upload & Ingestion
Upload a PDF, DOCX, TXT or Markdown file; it is validated, its text extracted with page and heading provenance, split into overlapping chunks, and persisted ready for embedding. `POST /documents/upload`.
- **Status:** Implemented
- **Dependencies:** Document Models & Migrations, Configuration Management

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
Provider-agnostic chat completions across OpenAI and OpenRouter, selected by configuration. The client is constructed lazily on first use, so importing the package requires no credentials.
- **Status:** Implemented — **no caller yet**, see note below
- **Dependencies:** None

### Prompt Registry System
`PromptTemplate`, `PromptRegistry` and `PromptBuilder`, with two built-in prompts: `assistant` and `ai_shadow` (the retrieval prompt shape).
- **Status:** Implemented — **no caller yet**
- **Dependencies:** None

### Analysis Engine
Runs a registered prompt, calls the LLM, tolerates markdown-fenced JSON, and validates the result against a caller-supplied Pydantic model.
- **Status:** Implemented — **no caller yet**
- **Dependencies:** Prompt Registry System, LLM Provider Abstraction

### Health & Root Endpoints
`GET /health`, `GET /`.
- **Status:** Implemented

> **On the three "no caller yet" entries.** The LLM layer, prompt system and Analysis Engine were carried over from the reference repository because the next feature — retrieval and cited chat — needs all three, and they are complete and tested rather than placeholder. They are deliberately unused by ingestion. If retrieval is not built, they should be removed rather than left indefinitely.

---

## In Progress

None. See [`ROADMAP.md`](ROADMAP.md).

---

## Planned

### Embedding Generation
Populate `document_chunks.embedding` via a provider-agnostic embedding service. Requires no schema change — the column and its index already exist.
- **Status:** Planned
- **Dependencies:** LLM Provider Abstraction, Document Models & Migrations

### Semantic Retrieval
Top-k similarity search over chunks using pgvector cosine distance, scoped by user and filtered by a similarity floor.
- **Status:** Planned
- **Dependencies:** Embedding Generation

### RAG Chat with Citations
`POST /chat` answering from retrieved chunks, returning structured citations resolved back to document, page and heading via the Analysis Engine.
- **Status:** Planned
- **Dependencies:** Semantic Retrieval, Analysis Engine, Prompt Registry System

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
