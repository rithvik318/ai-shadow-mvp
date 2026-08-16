# AI Shadow MVP — Features Catalog

The authoritative answer to "does X exist today". Where this document and [`ARCHITECTURE.md`](ARCHITECTURE.md) disagree, this document wins. For phase sequencing see [`ROADMAP.md`](ROADMAP.md).

---

## Implemented

### Document Upload & Ingestion
Upload a PDF, DOCX, PPTX, TXT or Markdown file; it is validated, its text extracted with page and heading provenance, split into overlapping chunks, embedded, and persisted. `POST /documents/upload`. A document reaches `indexed` only once every chunk carries a vector. Anything else — `.doc`, `.ppt`, `.vsd`, spreadsheets — is rejected with `415` and leaves no row.
- **Status:** Implemented
- **Dependencies:** Embedding Generation, Document Models & Migrations, Configuration Management

### Multi-File Upload
`POST /documents/batch-upload` takes many files in one request and reports each one separately: filename, `result`, `succeeded`, `document_id`, `status` and a `reason` where there is one. Files are processed independently, so an unreadable or unsupported file does not stop the ones after it — the response carries per-file outcomes rather than one verdict, and is `200` whatever the individual files did. `result` is one of `indexed`, `unchanged`, `replaced`, `failed` or `unsupported`. Reasons name what went wrong and never carry a traceback. Bounded by `MAX_BATCH_UPLOAD_FILES`; a larger batch is `413`, because ingestion is synchronous and holds the request open for the whole batch. Both upload endpoints call one ingestion service — there is no second pipeline.
- **Status:** Implemented
- **Dependencies:** Document Upload & Ingestion

### Document Identity & Idempotent Ingestion
Re-offering a file the knowledge base already holds does not duplicate it. Identity is `content_hash` — the sha256 of the uploaded bytes — optionally qualified by `source_uri`, a stable identifier for where the file came from. Filename is never an identity: two unrelated files are routinely both called `proposal.pdf`, and they stay separate. Same bytes already indexed is `unchanged`: nothing is re-parsed and nothing is re-embedded. Same `source_uri` with different bytes is `replaced`: the document is re-indexed in place, its old chunks deleted and new ones stored, so the previous text stops being retrievable. Without a `source_uri`, edited content is a new document — a plain upload carries nothing tying it to the earlier one. `content_hash` is nullable, and documents ingested before it existed carry `NULL`, which never compares equal to a digest and so is never wrongly deduplicated. `source_uri` and `source_version` are not settable through the API today; they exist for the synchronisation phase to populate.
- **Status:** Implemented
- **Dependencies:** Document Upload & Ingestion

### Document Lifecycle
`pending → processing → indexed | failed | unsupported`. A document is `indexed` only once every chunk carries a vector, so a document retrieval cannot see is never reported as searchable. `failed` records a document this system tried and could not finish — the reason is in `error_message`, and a retry may succeed. `unsupported` is separate because it will not succeed until a parser for that format exists; it is persisted only for ingestion carrying a `source_uri`, so a sync can stop re-offering a file it cannot read, while a hand upload of the same file is still simply rejected. Retrieval requires `indexed`, so `failed` and `unsupported` documents are unreachable from `/search` and `/chat` whatever chunks they may still hold.
- **Status:** Implemented
- **Dependencies:** Document Models & Migrations

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
Top-k cosine similarity search over stored chunk vectors, run in the database against the HNSW index. Configurable `top_k` and similarity floor, scoped by user, restricted to `indexed` documents, with duplicate content collapsed on a whitespace- and case-insensitive fingerprint, so the same passage under two filenames does not take two of the slots a caller asked for. Results carry document, filename, page and heading, so a citation can be resolved. Reached over HTTP through `POST /chat` and, without the model, through `POST /search`.
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

### Retrieval-Only Search
`POST /search` runs the retrieval half of the pipeline and stops: query embedding, vector search, deduplication and the similarity floor, returning ranked passages with `document`, `section`, `page`, `similarity` and the passage text. No prompt is rendered and no completion is requested, so relevance can be judged — and `RETRIEVAL_SIMILARITY_THRESHOLD` tuned — without paying for an answer or being persuaded by one. Same 1–50 `top_k` window as chat; nothing found is `200` with an empty result set.
- **Status:** Implemented
- **Dependencies:** Semantic Retrieval

### Knowledge Base Selection & Ingestion
A two-step, reproducible workflow for turning a large mixed corpus into the knowledge base. `python -m scripts.build_kb_manifest --corpus ...` walks the corpus read-only and writes `knowledge_base_manifest.json`, recording every file as included, excluded or needing review, with its reason, category, brand, derived year, and any `duplicate_of` or `superseded_by` relation. Years come from the filename or folder, never from a modification time, which OneDrive rewrote across this corpus. The policy is inclusive and has no per-category limits: a file is dropped only for being unreadable in the current parser, sensitive, a template or form, another organisation's material, a byte-identical duplicate, or a superseded issue of a selected document. The manifest also reports the unsupported formats it left behind, by count, size and largest candidates, so the gap between "ingested" and "complete" is visible. `python -m scripts.ingest_kb_manifest` sends the selected files through the existing upload endpoint, skips what is already indexed so a re-run resumes, continues past individual failures, verifies the database, and writes `knowledge_base_ingestion_results.json`. The source corpus is never modified; exclusion happens at selection time.
- **Status:** Implemented
- **Dependencies:** Document Upload & Ingestion

### Multi-User Digital Twin
Every Digital Twin has an owner. A `users` table holds name, email and role; `POST /users` and `GET /users` create and list them, and the `X-User-ID` request header names which one a call is acting as. Missing is `401`, malformed is `422`, and an id with no user is `404` — the header never creates a user. Profile, memory and the persona half of chat are scoped to that user and every query filters on it. The company knowledge base is deliberately *not* scoped this way: `documents.user_id` and `document_chunks.user_id` remain the shared `MVP_USER_ID`, because the corpus is company-wide and only the twin is private. This is identity, not authentication — the header is trusted as sent.
- **Status:** Implemented
- **Dependencies:** Digital Twin Profile & Memory, Database Connectivity Layer

### Digital Twin Profile & Memory
Who the Shadow answers for, and what it durably knows about them. One profile per user — name, role, organization, responsibilities, expertise, priorities, decision preferences, current focus and communication style — through `GET /profile` and `PUT /profile`, where a partial write updates the fields supplied and leaves the rest. Memories are typed (`fact`, `preference`, `decision`, `commitment`, `context`), carry an importance from 1 to 5, and can be retired rather than deleted: `GET /memory`, `POST /memory`, `PATCH /memory/{id}`, `DELETE /memory/{id}`, filterable by type and active flag. Every memory is written explicitly through the API; nothing is extracted from chat or from ingested documents.
- **Status:** Implemented
- **Dependencies:** Database Connectivity Layer

### Persona-Aware RAG
`POST /chat` loads the profile and the most important active, unexpired memories and puts them above the retrieved passages, under `[DIGITAL TWIN PROFILE]` and `[MEMORY]` headings, with the passages under `[KNOWLEDGE SOURCES]`. The prompt tells the model to use them for tone, emphasis and priorities but never as evidence: only the numbered passages can be cited as `[SOURCE n]`, and `sources` in the response is still built from retrieved chunks alone. The persona block is bounded by `PERSONA_CONTEXT_MAX_CHARS` and subtracted from `CHAT_CONTEXT_MAX_CHARS` rather than added to it, so a Digital Twin cannot grow the prompt past the bound already in place. With no profile and no memories the assembled context is byte-for-byte what it was before the feature existed. The request and response contracts are unchanged.
- **Status:** Implemented
- **Dependencies:** RAG Chat with Citations, Digital Twin Profile & Memory

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

### OneDrive Synchronization
Keeping the knowledge base in step with the corpus in OneDrive: discovering what changed, and calling the existing ingestion service with each file's `source_uri` and `source_version`. Nothing of it is built — no Microsoft Graph client, no delta query, no webhooks. The ingestion layer it will call is finished.
- **Status:** Planned
- **Dependencies:** Document Identity & Idempotent Ingestion

### Frontend
React and Tailwind interface for upload, document management, chat, and source display.
- **Status:** Planned
- **Dependencies:** RAG Chat with Citations

### Authentication
Verifying that a caller is who `X-User-ID` says they are. Multi-user data isolation already exists for the Digital Twin, so this is a change to where the identity comes from — a session or token replacing a trusted header — not a migration or a backfill.
- **Status:** Planned
- **Dependencies:** Multi-User Digital Twin

### Background Ingestion
Move ingestion off the request thread once documents are large enough for synchronous processing to be a problem.
- **Status:** Planned
- **Dependencies:** Document Upload & Ingestion

---

## Explicitly out of scope for the MVP

Autonomous agents, email, CRM, calendar, LangGraph and n8n are deferred entirely.

Carried over from the reference repository's design but deliberately not built here: the AI Orchestrator, conversation/user/task memory, the tool architecture (email, calendar, research, search), document generation, and multi-agent workflows.
