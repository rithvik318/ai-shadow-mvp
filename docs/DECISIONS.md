# AI Shadow MVP — Decision Log

Why the codebase is shaped the way it is. Entries are chronological, oldest first. Decisions carried over from the `ai-shadow` prototype are marked as such.

---

## FastAPI as the backend framework

- **Date:** 2026-07-21 *(carried from the reference repository)*
- **Decision:** Use FastAPI as the HTTP framework.
- **Rationale:** Native Pydantic integration for validation and settings, both already core to the design, and fast iteration during early development.
- **Consequences:** Routes and schemas follow FastAPI and Pydantic conventions.
- **Status:** Accepted — in effect.

---

## Provider abstraction via a single OpenAI-compatible client

- **Date:** 2026-07-22 *(carried)*
- **Decision:** Reach both OpenAI and OpenRouter through the `openai` SDK, changing only `api_key` and `base_url`.
- **Rationale:** OpenRouter exposes an OpenAI-compatible surface, so one client configured two ways avoids duplicated code and keeps provider selection in a single module.
- **Consequences:** Adding a provider is easy only if it is OpenAI-compatible. All provider-specific logic stays in `app/services/llm/client.py`.
- **Status:** Accepted — in effect.

---

## Prompt Registry architecture

- **Date:** 2026-07-23 *(carried)*
- **Decision:** Manage prompts as named, registered templates split across `PromptTemplate`, `PromptRegistry` and `PromptBuilder` rather than inline strings.
- **Rationale:** Keeps prompt text out of service and route code and makes every prompt discoverable through one registry.
- **Consequences:** New prompts are defined in `system.py` and registered in `register_default_prompts()`.
- **Status:** Accepted — in effect.

---

## Testing strategy: unit and integration tests mirroring application structure

- **Date:** 2026-07-23 *(carried)*
- **Decision:** pytest, with the test tree mirroring the application tree, and LLM calls always mocked.
- **Consequences:** Tests for a module are trivially locatable. No test makes a network call.
- **Status:** Accepted — in effect.

---

## Start a focused MVP repository rather than continuing the prototype

- **Date:** 2026-07-28
- **Decision:** Build the MVP in a new repository, carrying forward only the components it needs: configuration, exception hierarchy, database layer, LLM provider abstraction, prompt system, Analysis Engine, and the testing approach. Leave behind five empty service stubs, the email intelligence feature, four unused prompt templates, three never-raised exception classes, and the single-shot `/chat` route.
- **Context:** The prototype had 1,265 lines of Python delivering two endpoints, alongside an architecture document describing a system roughly ten times larger — an orchestrator, a four-part memory hierarchy, and a six-tool action layer. Carrying that structure forward would have meant maintaining scaffolding for features that are not on the MVP path.
- **Rationale:** A clean repository makes the MVP's actual surface visible, and lets the prototype's known issues be fixed in transit rather than inherited. The components that were carried are complete and tested; the ones left behind were placeholders or unused.
- **Consequences:** The prototype remains the reference for anything later reinstated. The LLM layer, prompt system and Analysis Engine have no caller until retrieval is built — an accepted, time-boxed exception to "no unused code", recorded in `FEATURES.md`.
- **Status:** Accepted — in effect.

---

## The prototype's `/chat` endpoint was not carried forward

- **Date:** 2026-07-28
- **Decision:** Omit the existing single-shot chat endpoint rather than porting it.
- **Context:** The prototype's `/chat` sent a user message straight to the LLM with no context, history or citations.
- **Rationale:** Phase 4 replaces it wholesale with retrieval-augmented chat. Carrying a version destined for immediate replacement would seed the new repository with exactly the unfinished architecture this migration set out to leave behind.
- **Consequences:** There is no chat surface until Phase 4. The `ai_shadow` prompt — which already has the `Context` / `Question` shape — is carried and ready for it.
- **Status:** Accepted — in effect.

---

## Chat Completions rather than the Responses API

- **Date:** 2026-07-28
- **Decision:** `LLMService.complete()` calls `client.chat.completions.create()`. The prototype called `client.responses.create()`.
- **Context:** The deployed configuration routes through OpenRouter, whose Responses API is documented as a beta surface, while Chat Completions is the interface every OpenAI-compatible provider implements.
- **Rationale:** The provider-independence decision above is only real if the call itself is portable. Using a beta, unevenly-supported surface undercut it silently.
- **Consequences:** Response parsing moves from `response.output_text` to `response.choices[0].message.content`. Revisit if the Responses API becomes universally supported and offers something Chat Completions does not.
- **Status:** Accepted — in effect.

---

## pgvector on Postgres, with the embedding column created up front

- **Date:** 2026-07-28
- **Decision:** Store embeddings as a `vector(1536)` column on `document_chunks`, created — with its HNSW index — in the baseline migration, nullable and unpopulated. Ship a `docker-compose.yml` using `pgvector/pgvector:pg16`.
- **Context:** The requirement was that embeddings be addable without a schema change. Alternatives were a JSON column (portable but unindexable, and it would need migrating before retrieval works) and a separate vector store such as Chroma.
- **Rationale:** Citations require joining a retrieved vector back to its document, page and heading. With pgvector that is one query; with a separate store it is a query, a lookup and application-side reconciliation. One datastore also means one backup story and one connection pool. Creating the index now over an empty column costs nothing, since HNSW builds incrementally.
- **Consequences:** Postgres with the pgvector extension is required, which is why the compose file exists rather than assuming a local install. The model declares the column as `Vector(...).with_variant(JSON(), "sqlite")` so the test suite runs on in-memory SQLite with no services present — the column exists in both dialects, which is what preserves the no-migration promise.
- **Status:** Accepted — in effect.

---

## Every row is user-scoped from the first migration

- **Date:** 2026-07-28
- **Decision:** `documents` and `document_chunks` both carry a `user_id`, every query filters on it, and ingestion runs as a single placeholder owner (`MVP_USER_ID`) until authentication exists. `user_id` is denormalised onto chunks rather than reached through a join.
- **Context:** Authentication is not on the MVP path, but documents are user data from the first upload.
- **Rationale:** Adding scoping later means a migration, a backfill, and an audit of every query written in the meantime. Adding it now costs a column and a filter. The denormalisation on chunks keeps retrieval — which filters by owner on every search — off a join.
- **Consequences:** Introducing authentication becomes a change to where `user_id` comes from. Until then the system is single-tenant and unauthenticated, recorded in `KNOWN_ISSUES.md`.
- **Status:** Accepted — in effect.

---

## Parsing and chunking are pure functions; ingestion owns the transaction

- **Date:** 2026-07-28
- **Decision:** `parser_service` and `chunker_service` take bytes and return dataclasses, with no database, configuration-at-import or network access. `ingestion_service` orchestrates them and owns the session.
- **Rationale:** Format handling is where the awkward cases live — scanned PDFs, encodings, headings, blank pages — and it is far cheaper to test as pure functions than through the ORM. It also keeps the pipeline stages independently reusable.
- **Consequences:** The parser cannot enforce the configured size limit; validation lives in `ingestion_service.validate_upload()` instead.
- **Status:** Accepted — in effect.

---

## Validation failures store nothing; parse failures store a failed document

- **Date:** 2026-07-28
- **Decision:** Uploads rejected for size, type or emptiness leave no row. Files that pass validation but fail to parse are persisted with `status="failed"` and an `error_message`, then the error is re-raised.
- **Rationale:** A rejected upload is a client mistake with an immediate, self-explanatory response; storing it would be noise. A parse failure is something the user needs to see afterwards — "why is my scanned PDF not searchable" is answerable only if the failure is recorded.
- **Consequences:** `GET /documents?status=failed` is the diagnostic surface. The upload response is still an error, so clients cannot mistake a failed ingest for a successful one.
- **Status:** Accepted — in effect.

---

## Ingestion is synchronous

- **Date:** 2026-07-28
- **Decision:** Parse, chunk and persist inside the request.
- **Rationale:** At MVP document sizes this keeps the API honest — the response describes the final state — and avoids a job queue, a worker process and a polling endpoint before anything needs them.
- **Consequences:** Large documents will hold a request open. Tracked in `KNOWN_ISSUES.md`; revisit when real documents make it a problem, not before.
- **Status:** Accepted — in effect.

---

## Chunking uses `langchain-text-splitters`, not a hand-rolled splitter

- **Date:** 2026-07-28
- **Decision:** Depend on `langchain-text-splitters` for `RecursiveCharacterTextSplitter`, rather than implementing recursive paragraph/sentence/word splitting in-repo.
- **Context:** The alternative was roughly fifty lines of our own code, avoiding a dependency whose transitive tree is heavier than the feature warrants.
- **Rationale:** The splitting strategy is well-specified and easy to get subtly wrong at boundaries. Taking the splitter package alone — not LangChain itself — gets a tested implementation without adopting chains, agents or the framework's abstractions.
- **Consequences:** One dependency carrying more transitive weight than its use. If that becomes a problem, the splitter is used in exactly one place and is straightforward to replace.
- **Status:** Accepted — in effect.

---

## Domain errors are mapped to HTTP status codes in one place

- **Date:** 2026-07-28
- **Decision:** Exception handlers registered on the application map `DocumentError` and `LLMServiceError` subclasses to status codes. Routes and services never build `HTTPException`.
- **Context:** The prototype raised `AnalysisValidationError` with no handler anywhere, so malformed model output surfaced as an opaque 500.
- **Rationale:** One mapping table beats a `try/except` in every route, and services stay free of HTTP concepts.
- **Consequences:** Adding an error type means adding it to `_DOCUMENT_ERROR_STATUS` in `app/main.py`. Unmapped `DocumentError` subclasses fall back to `400`.
- **Status:** Accepted — in effect.

---

## Enum values, not names, are persisted

- **Date:** 2026-07-28
- **Decision:** The `status` column uses `values_callable` so `DocumentStatus.INDEXED` stores `"indexed"`.
- **Context:** SQLAlchemy persists a PEP-435 enum by member *name* by default, which would have written `"INDEXED"` while the migration's CHECK constraint expects `"indexed"`.
- **Rationale:** The stored form should match the API's wire format and the migration's constraint. Without this the two silently diverge.
- **Consequences:** Renaming a member is safe; changing its value is a data migration.
- **Status:** Accepted — in effect.

---

## Database fixtures live outside the root `conftest.py`

- **Date:** 2026-07-28
- **Decision:** The root `conftest.py` only resets the prompt registry. The in-memory database fixture lives in `tests/support/database.py` and is re-exported by the two directories that need it.
- **Rationale:** Parser and chunker tests are pure functions over bytes. A root `conftest.py` importing the engine would make every test in the suite depend on database packages being importable.
- **Consequences:** Two thin `conftest.py` files re-export one fixture instead of one declaring it.
- **Status:** Accepted — in effect.

---

## Template for new decisions

```markdown
## <Short decision title>

- **Date:** YYYY-MM-DD
- **Decision:** <What was decided>
- **Context:** <What prompted it>
- **Rationale:** <Why this over the alternatives>
- **Consequences:** <What it commits us to, and the tradeoff accepted>
- **Status:** <Proposed | Accepted — in effect | Superseded by <link>>
```
