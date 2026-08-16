# AI Shadow MVP — Roadmap

The MVP is one sentence: **users upload documents, the documents are indexed, users chat with them, and answers carry citations.** Everything here serves that sentence. Capabilities outside it are tracked in [`FEATURES.md`](FEATURES.md) under "explicitly out of scope".

---

## Current phase

**Phase 6 — Document ingestion and knowledge base foundation. Complete.**

Where things stand right now, in one page: [`PROJECT_STATE.md`](PROJECT_STATE.md).

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
- Retrieval-only search: `POST /search` returns the ranked passages with their text and no model call, so the similarity floor can be judged against real documents.
- Multi-file upload, over one ingestion service shared with the single-file path: `POST /documents/batch-upload` processes each file independently and reports a result for each, so one unreadable or unsupported file no longer decides the fate of the batch.
- Document identity that is not the filename — a content hash, plus an optional source identifier and version. Re-offering an unchanged file is a skip rather than a duplicate, and a changed file from a known source is re-indexed in place with its old chunks removed, so replaced text stops being retrievable.
- Multi-user Digital Twin: a `users` table, `X-User-ID` as the MVP identity mechanism, and profile, memory and chat scoped to the identified user. The company knowledge base stays shared; only the twin is private.
- Digital Twin core: a single profile and a typed, explicitly-written memory store, both loaded into every chat above the retrieved passages and clearly separated from them — profile and memory shape tone and priorities, and only documents can be cited.
- Knowledge-base selection and ingestion: a deterministic manifest chooses which corpus documents belong in the knowledge base and records why for every file, with no per-category limits, and a second script ingests exactly that list through the existing upload endpoint and verifies what landed. Legacy formats the parser cannot read — `.doc`, `.ppt`, `.vsd`, spreadsheets — are reported rather than quietly dropped, and are the largest remaining gap in coverage.

---

## Next: Phase 7 — OneDrive synchronization

The ingestion layer it will call is finished: one service, an identity that
survives a file being edited or renamed, per-file outcomes, and a record of what
could not be read. What remains is everything specific to OneDrive — a Microsoft
Graph client, change discovery, and deciding how often to run — none of which is
built.

Settle alongside it, on real documents: the similarity floor. The 0.0 default excludes only passages pointing the opposite way, which is permissive — it hands the model loosely related context rather than admitting there is none. Too high and the system says "I don't know" about documents it holds. Now that answers are visible, this is the single number most likely to make them feel wrong, and it can finally be judged by reading the output.

`POST /search` now exists for exactly that purpose — retrieval without the model — so the floor can be moved and the effect read off directly rather than inferred from an answer.

---

## Then

**Phase 8 — Frontend.** React and Tailwind: upload with progress and indexing status, document list, chat, and a source panel rendering each citation as document and page.

**Phase 9 — Hardening.** Authentication behind the `X-User-ID` identity that now exists, CI, background ingestion, and whatever the first real documents expose about extraction quality.

---

## Deliberately deferred

Autonomous agents, email, CRM, calendar, LangGraph and n8n — deferred entirely, including partial or exploratory versions.

The AI Orchestrator, the conversation/task memory system, and the tool architecture are all designed in the reference repository and none of them are needed for the MVP sentence above. They should not be built until something concretely requires them.
