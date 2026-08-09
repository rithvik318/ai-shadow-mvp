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
- **Consequences:** Postgres with the pgvector extension is required, which is why the compose file exists rather than assuming a local install. The model declares the column as `Vector(...).with_variant(JSON(none_as_null=True), "sqlite")` so the test suite runs on in-memory SQLite with no services present — the column exists in both dialects, which is what preserves the no-migration promise. The `none_as_null=True` is not decoration: SQLAlchemy's `JSON` defaults to storing a Python `None` as the JSON encoding of `null` rather than SQL NULL, which made `embedding IS NULL` match nothing on SQLite while matching correctly on pgvector. A variant that disagrees with production about a value's meaning is worse than no variant, because the suite still passes. Any future `with_variant` needs the same scrutiny.
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

## The embedding provider is configurable independently of the completion provider

- **Date:** 2026-07-29
- **Decision:** `EMBEDDING_PROVIDER` overrides `LLM_PROVIDER` for embeddings only, and clients are cached per provider name rather than as one singleton. Unset means "same provider as completions".
- **Context:** OpenRouter announced an embeddings endpoint on 16 July 2026 at `POST /api/v1/embeddings`, which is the path the OpenAI SDK produces against our configured `base_url`. It is not yet in the API reference, and we have not made a live call against it.
- **Rationale:** The provider-independence decision is worth nothing if an unavailable capability at one provider forces a code change. Making the embedding provider a separate setting means the fallback — embeddings to OpenAI, completions unchanged — is an `.env` edit. `scripts/verify_embedding_provider.py` turns the open question into a ten-second check.
- **Consequences:** Two providers may be live at once, so two API keys may be needed. `get_client()` caches per provider to avoid rebuilding either.
- **Status:** Accepted — in effect.

---

## A document is not `indexed` until its chunks are embedded

- **Date:** 2026-07-29
- **Decision:** Ingestion embeds before setting `status="indexed"`. An embedding failure marks the document `failed` and returns `502`.
- **Context:** Before this change, ingestion stored chunks with a null `embedding` and reported `indexed`.
- **Rationale:** `indexed` should mean retrievable. A document with no vectors is invisible to similarity search, and the failure is silent: the user asks a question, gets "nothing relevant found", and has no way to tell that from a genuinely unrelated question. Making the status honest turns a silent failure into a visible one.
- **Consequences:** Upload latency now includes an embedding round trip. Documents ingested before this change still report `indexed` while having no vectors — `scripts/backfill_embeddings.py` exists to correct exactly that.
- **Status:** Accepted — in effect.

---

## Embedding failures keep the parsed chunks; parse failures do not

- **Date:** 2026-07-29
- **Decision:** When embedding fails, the document is marked `failed` but its chunks are kept, and `embed_document_chunks` is idempotent so a later run completes it.
- **Rationale:** The two failures are not alike. A parse failure is deterministic — the same bytes will fail again, so keeping partial output is clutter. An embedding failure is usually a transient provider outage, and the parse work is still valid and already paid for. Keeping it means recovery is a backfill rather than a re-upload, which matters when the user no longer has the file to hand.
- **Consequences:** `failed` can mean two things, distinguished by whether chunks exist. `count_unembedded_chunks()` is the single query that tells them apart.
- **Status:** Accepted — in effect.

---

## Embedding stays synchronous, and the stack stays sync

- **Date:** 2026-07-29
- **Decision:** Embedding runs inside the upload request. No `async` was introduced.
- **Context:** Async was requested for the retrieval layer. Embedding is I/O-bound, so it is the obvious candidate.
- **Rationale:** The benefit is real but the cost is a fractured stack: SQLAlchemy's sync `Session`, the sync session dependency, and every existing service are synchronous, so an async embedding path would either block the event loop anyway or force `asyncpg` and an async session across the whole codebase. That is a coherent change to make deliberately, not a side effect of one feature. Batching already removes most of the latency this would address — one call for sixty-four chunks, not sixty-four calls.
- **Consequences:** Upload holds a worker thread for the duration of the embedding call. Going async later is a single planned migration rather than an accumulation of half-async paths.
- **Status:** Accepted — in effect.

---

## Vectors are matched to chunks by the provider's index, not by arrival order

- **Date:** 2026-07-29
- **Decision:** `EmbeddingService` sorts the response by each item's `index` before zipping vectors to inputs, and rejects a response whose length differs from the batch.
- **Rationale:** The API documents order preservation, but a reordering would attach every vector to the wrong chunk, and nothing downstream could detect it — retrieval would return confidently wrong citations. Sorting on a field the response already carries costs nothing and removes the need to trust the guarantee.
- **Status:** Accepted — in effect.

---

## Cosine distance is one dialect-aware construct, guarded by a real-pgvector test

- **Date:** 2026-07-30
- **Decision:** `app/database/vector.py` defines a `cosine_distance` construct compiling to pgvector's `<=>` on Postgres and to a `cosine_distance(a, b)` call on SQLite, which the test fixtures register as a Python function. One opt-in test (`-m postgres`) runs the identical `search()` against a live pgvector database and asserts the same ordering and the same similarity values.
- **Context:** Similarity search is a Postgres operator, and the suite runs on SQLite by an earlier decision. The alternatives were to require a live database for retrieval tests, or to rank in Python.
- **Rationale:** Ranking in Python would ignore the HNSW index and degrade linearly with corpus size — it solves a testing problem by making production worse. Requiring a live database would cost the service-free suite and block CI. The construct keeps exactly one query in production code while leaving it executable in tests. The pgvector test exists because a stand-in that silently disagrees with the real thing is precisely how the PR 1 NULL-semantics defect survived review: a green suite proved nothing about production. Asserting the two agree, rather than assuming it, is the whole point.
- **Consequences:** A dialect difference now lives in one file rather than in the service. The SQLite function is registered by test fixtures, not by the application, because SQLite is not a supported production dialect. Any future vector operator needs the same treatment and the same paired test.
- **Status:** Accepted — in effect.

---

## Search is restricted to `indexed` documents

- **Date:** 2026-07-30
- **Decision:** Retrieval joins `documents` and requires `status = indexed`. Chunks with no vector are excluded, as are rows whose computed distance is NULL.
- **Rationale:** A document mid-ingestion, or one whose embedding step failed, holds only part of itself. Answering from it would produce a confident citation drawn from a fragment, which is worse than not answering — and the user has no way to tell the difference. The NULL-distance filter is not decoration either: a stored vector of the wrong width or of zero length has no defined similarity, and SQLite and Postgres sort NULLs to opposite ends, so leaving them in would give two dialects two different answers.
- **Consequences:** A failed document stays invisible to search until the backfill completes it, which is the intended relationship between the two features.
- **Status:** Accepted — in effect.

---

## An empty corpus is checked before the query is embedded

- **Date:** 2026-07-30
- **Decision:** `search()` probes for a single eligible chunk before calling the embedding provider, and returns `[]` if there is none.
- **Rationale:** On a fresh install the common case is no documents at all. Embedding first would charge for a call whose result cannot match anything, and — with no key configured yet — would surface a provider error where the honest answer is "there is nothing here". The probe is one indexed lookup with `LIMIT 1`.
- **Consequences:** One extra cheap query per search. Worth it to keep an empty knowledge base a normal state rather than a failure.
- **Status:** Accepted — in effect.

---

## Cosine similarity, not L2 or inner product

- **Date:** 2026-07-30
- **Decision:** Rank by cosine similarity, via pgvector's `<=>` cosine-distance operator. Similarity is reported to callers as `1 - distance`, in `[-1, 1]`.
- **Context:** pgvector offers L2 (`<->`), inner product (`<#>`) and cosine (`<=>`), and the choice interacts with the index built in migration 0001.
- **Rationale:** Four reasons, in descending order of how much getting it wrong would hurt. First, the HNSW index is built `USING hnsw (embedding vector_cosine_ops)`, and an index is only usable by the operator class it was built for — searching with L2 would return correct answers while silently falling back to a sequential scan, which is the kind of failure that looks fine in testing and becomes catastrophic at scale. Second, cosine is invariant to magnitude, so a long chunk is not scored differently from a short one purely for having a larger norm; L2 over unnormalised vectors conflates "different meaning" with "different length", which is precisely the wrong bias when chunks vary in size by design. Third, the `[-1, 1]` bound makes the configurable threshold a number a human can reason about and carry between models, where an L2 threshold is unbounded and specific to one embedding space. Fourth, `openai/text-embedding-3-small` returns unit-normalised vectors, for which cosine and inner product rank identically — so cosine costs nothing today and stays correct if a model that does not normalise is adopted later.
- **Consequences:** Changing the metric means changing the index, in a migration, and re-tuning the threshold. The chosen metric is documented at the top of `retrieval_service.py` so nobody has to infer it from an operator.
- **Status:** Accepted — in effect.

---

## The similarity floor is configurable and can be switched off

- **Date:** 2026-07-30
- **Decision:** `RETRIEVAL_SIMILARITY_THRESHOLD` sets the floor and may be empty to disable filtering entirely; `search(similarity_threshold=...)` overrides it per call, and passing `None` disables the floor for that call. A module-level `UNSET` sentinel distinguishes "argument omitted" from "explicitly disabled".
- **Rationale:** No floor is right for every corpus, and the right value can only be found by measuring against real documents — so the value must not be baked into the code, and "no floor at all" has to be reachable while tuning. Without the sentinel, `None` would be indistinguishable from "not supplied", leaving no way to disable a floor that configuration had turned on.
- **Consequences:** Excluding rows whose distance is undefined can no longer ride along on the threshold comparison, because there may not be one. `distance IS NOT NULL` is now an explicit predicate, with its own test.
- **Status:** Accepted — in effect.

---

## Deduplication pages the ranking rather than over-fetching a fixed multiple

- **Date:** 2026-07-30
- **Decision:** Results drop a chunk whose content exactly matches an earlier, higher-scoring result. The query is paged — ordered by `(distance, id)` for a stable sequence — until `top_k` unique chunks are collected or the corpus is exhausted, subject to a candidate ceiling that is logged when reached.
- **Context:** The first implementation fetched `2 × top_k` and deduplicated within it. That is simpler, but no fixed multiple can guarantee `top_k` unique results against an unknown number of duplicates: a document whose first twenty chunks are the same header would quietly return one result where five were asked for.
- **Rationale:** Deduplication is supposed to improve the result set, not shrink it. Paging costs an extra round trip only when duplicates are actually present — the common case still completes in one. The `id` tiebreak matters more than it looks: without a deterministic order, two chunks at equal distance can swap between pages and be returned twice or skipped entirely.
- **Consequences:** A pathological corpus is bounded by the candidate ceiling rather than paging indefinitely, and truncation there is logged rather than passed over in silence. Near-duplicate detection is still deliberately not attempted: chunk overlap makes neighbours share text by design, and a "close enough to be the same" threshold is a judgement that should not be made silently inside a search function.
- **Status:** Accepted — in effect.

---

## Chat sources come from retrieval, not from the model

- **Date:** 2026-07-31
- **Decision:** `POST /chat` returns the passages that were retrieved and placed in the prompt. The model produces only prose; it is never asked to name its sources, and `AnalysisEngine` is not used.
- **Context:** The alternative, sketched in earlier architecture notes, was to have the model cite by index and validate those indices through `AnalysisEngine` against a `RagAnswer` schema.
- **Rationale:** A source drawn from retrieval cannot be fabricated — the model has no way to name a document that was not fetched. Model-produced citations can point at the wrong passage, or at an index that does not exist, and validation only catches the second. For an MVP whose whole promise is "answers you can check", a citation that is structurally incapable of being invented is worth more than one that is finer-grained but occasionally wrong.
- **Consequences:** Attribution is at the level of the request, not the sentence: every retrieved passage is listed, including any the model ignored. Moving to sentence-level attribution means adopting the index-and-validate approach, and is tracked in `FEATURES.md`. `AnalysisEngine` consequently still has no caller.
- **Status:** Accepted — in effect.

---

## An empty or irrelevant knowledge base is answered, not raised

- **Date:** 2026-07-31
- **Decision:** When retrieval returns nothing, chat returns HTTP 200 with a fixed "I could not find anything in your documents" answer, no sources, and `retrieved_chunks: 0`. The model is not called.
- **Rationale:** Having no relevant documents is a normal state, not a failure — on a fresh install it is the *expected* state. Raising would force clients to treat it as an error path, and `retrieved_chunks` already lets them distinguish it from a grounded answer without inspecting prose. Skipping the model call matters more than the status code: asking a model to answer with no context invites exactly the invention the prompt spends five rules forbidding, and charges for it.
- **Consequences:** A caller must read `retrieved_chunks`, not just `answer`, to know whether anything was found. Provider failures still return 502, so the two cases never blur.
- **Status:** Accepted — in effect.

---

## `RetrievalError` maps to 422, separately from provider failures

- **Date:** 2026-07-31
- **Decision:** A dedicated handler maps `RetrievalError` — a blank question, an out-of-range `top_k` — to 422, leaving `LLMServiceError` and its subclasses on 502.
- **Rationale:** One is the caller's mistake and the other is not, and the status code is what tells a client whether retrying unchanged could ever work. Collapsing them would tell a client to retry a request that will always fail, or to give up on one that would succeed in a minute.
- **Status:** Accepted — in effect.

---

## `ai_shadow` was replaced by `rag_answer` rather than kept alongside it

- **Date:** 2026-07-31
- **Decision:** The `ai_shadow` prompt, carried from the prototype as a placeholder for this feature, is removed; `rag_answer` takes its place with the grounding rules the feature actually needs.
- **Rationale:** `ai_shadow` existed only to hold the shape of a retrieval prompt until chat was built. Shipping both would leave one permanently unused, which the operating manual forbids and which earlier entries in this log flagged as debt to settle. Its two-line instruction was also too weak for the job: the prompt now states explicitly that context is the only source of truth, forbids introducing facts, requires saying so when the answer is absent, and asks for synthesis across passages rather than a passage-by-passage summary.
- **Consequences:** `EXPECTED_PROMPT_NAMES` in `tests/prompts/test_system.py` changed to match — a factual update to the prompt set, not a relaxed assertion. `assistant` remains registered and still has no caller.
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
