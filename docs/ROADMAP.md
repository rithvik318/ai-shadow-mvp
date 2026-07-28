# AI Shadow MVP — Roadmap

The MVP is one sentence: **users upload documents, the documents are indexed, users chat with them, and answers carry citations.** Everything here serves that sentence. Capabilities outside it are tracked in [`FEATURES.md`](FEATURES.md) under "explicitly out of scope".

---

## Current phase

**Phase 1 — Ingestion. Complete.**

---

## Completed

- Repository established as a focused MVP build, carrying forward the reusable components of the `ai-shadow` prototype and leaving its unbuilt architecture behind (see [`DECISIONS.md`](DECISIONS.md)).
- Configuration, exception hierarchy, database layer, LLM provider abstraction, prompt system and Analysis Engine ported, with the reference repository's known issues fixed in transit: correct package `__init__.py` files, a lazily-constructed LLM client, SQL echo driven by `DEBUG`, declared test dependencies, and an `.env.example`.
- `documents` and `document_chunks` schema with an Alembic baseline migration, the pgvector extension, a nullable embedding column and its HNSW index.
- Document parsing for PDF, DOCX, TXT and Markdown, preserving page numbers and section headings.
- Configurable chunking with overlap, propagating provenance onto every chunk.
- Transactional ingestion with a visible document lifecycle (`pending → processing → indexed | failed`).
- Document API: upload, list with filtering and pagination, retrieve, delete.
- Domain errors mapped to specific HTTP status codes with a consistent body.
- Test suite covering parsing, chunking, ingestion, validation and the HTTP surface.
- Ruff configured for linting and formatting.

---

## Next: Phase 2 — Embeddings

Populate `document_chunks.embedding` through a provider-agnostic embedding service built on the existing LLM client. Requires no schema change.

The one open question to settle first: whether `client.embeddings.create()` works against OpenRouter's OpenAI-compatible embeddings endpoint, or whether embeddings should call OpenAI directly. Time-box that check before building.

---

## Then

**Phase 3 — Retrieval.** Top-k cosine similarity search over chunks, scoped by user, with a similarity floor and metadata returned alongside content.

**Phase 4 — RAG chat with citations.** `POST /chat` retrieving context and answering through the Analysis Engine against a `RagAnswer` schema, so citations arrive as validated structured data rather than prose. Explicit handling for an empty knowledge base and for no chunk clearing the floor: the model says it does not know rather than inventing an answer.

**Phase 5 — Frontend.** React and Tailwind: upload with progress and indexing status, document list, chat, and a source panel rendering each citation as document and page.

**Phase 6 — Hardening.** Authentication and real per-user scoping, CI, background ingestion, and whatever the first real documents expose about extraction quality.

---

## Deliberately deferred

The AI Orchestrator, the memory system, and the tool architecture are all designed in the reference repository and none of them are needed for the MVP sentence above. They should not be built until something concretely requires them.
