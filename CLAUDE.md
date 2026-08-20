# CLAUDE.md

## Operating manual — AI Shadow MVP

How work gets done in this repository. Applies equally to human contributors and to AI agents. It describes **how we work**, not **what exists** — for that, see [`docs/FEATURES.md`](docs/FEATURES.md).

---

## 1. What this project is

Users upload documents, the documents are indexed, users chat with them, and answers carry citations. A Digital Twin — one profile and a typed memory store per user — shapes how those answers are written, without ever becoming a citable source.

That is the whole scope. This repository was deliberately started fresh from the `ai-shadow` prototype to keep it that way — see [`docs/DECISIONS.md`](docs/DECISIONS.md). The single most common way to damage it is to build toward the prototype's larger architecture (orchestrator, memory hierarchy, tool layer) instead of toward that sentence.

Before adding anything, ask: does upload → index → chat → citations need this? If not, it goes in `docs/ROADMAP.md`, not in the codebase.

---

## 2. Workflow

```
Understand → Plan → Discuss → Implement → Test → Review → Document → Commit
```

**Understand.** In a new session, read `docs/PROJECT_STATE.md` first — it is the current checkpoint. Then check `docs/FEATURES.md` before touching a module, and read the surrounding code rather than inferring behaviour from names.

**Plan.** For anything beyond a trivial fix, state the approach and name the files that will change before writing code.

**Discuss.** Surface ambiguity and tradeoffs rather than silently picking. If a request could reasonably be solved several ways, say so first.

**Implement.** The smallest change that solves the stated problem, following existing patterns.

**Test.** New behaviour ships with tests. A change is not done until it is tested.

**Review.** Check against §5 and confirm nothing unrelated crept in.

**Document.** If the change alters what is implemented, uncovers an issue, or makes a non-obvious call, update the owning document in the *same* change.

**Commit.** See §8.

---

## 3. Principles

- **Scope discipline first.** Deferring a feature is cheap; removing one that grew roots is not.
- **Provider abstraction.** Business logic never depends on a specific LLM or embedding vendor. Switching providers is a configuration change, and only `app/services/llm/client.py` knows a provider's name.
- **Prompt abstraction.** Prompts are registered templates, never inline strings in services or routes.
- **Pure where possible.** Parsing and chunking are functions over data, with no database, network or import-time configuration. Push I/O to the edges.
- **Domain errors, not HTTP errors.** Services raise from `app/core/exceptions.py`. Only `app/main.py` knows status codes.
- **No premature abstraction.** Three similar lines beat a speculative abstraction. Do not add a layer for a second caller that does not exist.
- **No rewrite without a stated reason.** Working code is not improved by being retyped. Reformatting, renaming or restructuring a module that the task did not require is an unreviewable change hiding inside a reviewable one.
- **No unused code.** Ship it or leave it out. The Analysis Engine's exception is now closed — the Email Agent calls it. What remains uncalled is the registered `assistant` prompt, recorded in `docs/KNOWN_ISSUES.md`.
- **Documentation evolves with code.** A behaviour change is incomplete until its document is updated in the same change.

---

## 4. Environment

```bash
docker compose up -d               # Postgres with pgvector

cd backend
python -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env

alembic upgrade head
uvicorn app.main:app --reload
```

Tests: `pytest` from `backend/`. They use in-memory SQLite and need no services or credentials — keep it that way.

Lint and format: `ruff check app tests alembic` and `ruff format app tests alembic`.

---

## 5. Coding standards

- **Type hints** on every function signature and class attribute.
- **Pydantic** for all request/response schemas and configuration.
- **Custom exceptions** extend the hierarchy in `app/core/exceptions.py`; never raise bare `Exception` for a domain error.
- **Docstrings only where behaviour is non-obvious** — an invariant, a constraint, or why something is done a particular way. Do not restate what the name already says.
- **Comments explain why, never what.**
- **Package directories always have `__init__.py`** — double underscores. The prototype had two misnamed and nine missing; do not reintroduce that.
- **`ruff` is the formatter and linter.** Run both before committing.

---

## 6. Adding to the ingestion pipeline

**A new document format:** add its content type and extension to `app/core/constants.py`, write a `_parse_*` function in `parser_service.py` returning `ParsedSection`s with whatever provenance the format exposes, register it in `_PARSERS`, and add a fixture builder in `tests/fixtures/factories.py` plus parser tests covering a well-formed file, a corrupt one, and one with no text.

**A new error case:** add the exception to `app/core/exceptions.py` under `DocumentError`, add it to `_DOCUMENT_ERROR_STATUS` in `app/main.py`, and cover both the service-level raise and the HTTP status in tests.

**A new prompt:** define it in `app/prompts/system.py`, add it to `register_default_prompts()`, and update `EXPECTED_PROMPT_NAMES` in `tests/prompts/test_system.py`. Never inline a prompt string in a service or route. Rendering runs `str.format` over *both* the system and user prompts, so any literal brace — a JSON example, most often — must be doubled; `test_every_email_prompt_renders_with_its_variables` catches the ones that are not.

---

## 6a. Adding an email provider

`EmailProvider` in `app/services/email/provider/base.py` is the whole contract. Write a module beside `outlook_provider.py` that satisfies it, add one entry to `_BUILDERS` in `registry.py`, and add the settings it needs to `app/config/settings.py` and `.env.example`. Nothing above the provider boundary changes — that is the claim the boundary exists to make, and a change that requires touching a service means the abstraction leaked.

Two rules are not negotiable. **Nothing in `app/services/email/provider/` may import from `app/models/`, `app/schemas/` or `app/services/features/`** — a provider knows addresses, messages and bytes, not what a draft or a user is. And **no provider may return a `SendReceipt` for a message it did not send**: there is deliberately no null, in-memory or demo provider, because one would make every "Sent" badge in the product indistinguishable from a real one.

---

## 7. Testing standards

- Tests live in `backend/tests/`, mirroring `backend/app/`.
- One test module per component, plus integration tests where components meet.
- **Tests never make network calls.** LLM and embedding calls are always mocked.
- **Tests never require credentials or a running service.** This is what makes CI possible; the prototype could not have CI because importing the app needed a live API key.
- Database fixtures live in `tests/support/database.py`, re-exported by the directories that need them — not in the root `conftest.py`, so that pure-function tests do not depend on database packages.
- Binary fixtures are generated by `tests/fixtures/factories.py`, not committed as blobs.

---

## 8. Git workflow

- **Branches:** `feature/*`, `bugfix/*`, `refactor/*`, `docs/*`.
- **Commits** are small and scoped to one concern; messages are short, imperative, and explain *why* where the diff does not.
- **`main` stays green.**
- **One concern per pull request.** Do not bundle a refactor with a fix.
- **Never commit `.env`** or any file containing secrets.
- **Never commit or push unless explicitly asked to.** Leave the work in the tree and say what changed; staging and committing are the owner's call, not a tidy-up step at the end of a task.

---

## 9. Security

- Secrets are read only through `app/config/settings.py`, never hardcoded, never logged.
- **The assistant never sends.** An email leaves only after an explicit `POST /email/drafts/{id}/approve` followed by `/send`, and any edit to a draft withdraws that approval. Do not add a code path in which generation, approval or sending happen in one call, and do not give a model or a prompt a route to a provider.
- **Two owner concepts, deliberately separate.** The company knowledge base is *shared*: `documents.user_id` and `document_chunks.user_id` hold the string `MVP_USER_ID` for everyone. The Digital Twin is *private*: `digital_twin_profile.user_id` and `digital_twin_memory.user_id` are UUID foreign keys into `users`. Do not merge them and do not widen either scope.
- **Every Digital Twin query filters on `user_id`.** A read that omits it is one person's Shadow answering with another's memories — a leak, not a missing filter.
- `X-User-ID` is a development identity header, **not authentication**: it is trusted exactly as sent. Treat the absence of auth as a known limitation (`docs/KNOWN_ISSUES.md`), not as licence to add unscoped queries.

---

## 10. Documentation ownership

| Document | Owns |
|---|---|
| `docs/PROJECT_STATE.md` | Where the project stands right now — read first |
| `docs/ARCHITECTURE.md` | System design |
| `docs/ROADMAP.md` | Phases and what is next |
| `docs/FEATURES.md` | What exists right now — authoritative |
| `docs/KNOWN_ISSUES.md` | Gaps, limitations, accepted tradeoffs |
| `docs/DECISIONS.md` | Why the codebase is shaped this way |
| `README.md` | Getting started and the API reference |
| `CLAUDE.md` | How we work |

Exactly one document owns each kind of fact, and no fact is restated in a second document that does not own it. A change that alters implementation status, uncovers an issue, or makes a non-obvious call updates the matching document in the same change — not as a follow-up.

**Precedence when two documents disagree:** `PROJECT_STATE.md` → `FEATURES.md` → `ARCHITECTURE.md`. The checkpoint beats the catalogue, and the catalogue beats the design.
