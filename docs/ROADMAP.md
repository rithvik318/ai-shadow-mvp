# AI Shadow MVP — Roadmap

The MVP is one sentence: **users upload documents, the documents are indexed, users chat with them, and answers carry citations.** Everything here serves that sentence. Capabilities outside it are tracked in [`FEATURES.md`](FEATURES.md) under "explicitly out of scope".

---

## Current phase

**Phase 4 — RAG chat. Complete.**

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
- Embedding generation wired into ingestion, with batching, order-preserving vector assignment, and dimension validation against the column width.
- Embedding provider configurable independently of the completion provider, so a provider that does not serve embeddings is an environment change rather than a code change.
- Backfill for chunks with no vector, covering documents ingested before embeddings existed and uploads whose embedding step failed.
- Semantic retrieval: top-k cosine search executed in the database, configurable `top_k` and similarity floor, restricted to `indexed` documents and scoped by user.
- Dialect-aware `cosine_distance`, so the retrieval query is exercised by the SQLite suite and verified against real pgvector by an opt-in test.
- Stateless RAG chat: `POST /chat` answers from retrieved passages through the registered `rag_answer` prompt, returning the passages the model was shown.

---

## Next: Phase 5 — Frontend

React and Tailwind over the API that now exists end to end.

Settle first, on real documents: the similarity floor. The 0.0 default excludes only passages pointing the opposite way, which is permissive — it hands the model loosely related context rather than admitting there is none. Too high and the system says "I don't know" about documents it holds. Now that answers are visible, this is the single number most likely to make them feel wrong, and it can finally be judged by reading the output.

Worth adding alongside it: `POST /search`, exposing retrieval without the model, which makes that tuning a great deal easier.

---

## Then

**Phase 5 detail —** React and Tailwind: upload with progress and indexing status, document list, chat, and a source panel rendering each citation as document and page.

**Phase 6 — Hardening.** Authentication and real per-user scoping, CI, background ingestion, and whatever the first real documents expose about extraction quality.

---

## Deliberately deferred

The AI Orchestrator, the memory system, and the tool architecture are all designed in the reference repository and none of them are needed for the MVP sentence above. They should not be built until something concretely requires them.
