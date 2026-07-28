# CLAUDE.md

## Operating manual — AI Shadow MVP

How work gets done in this repository. Applies equally to human contributors and to AI agents. It describes **how we work**, not **what exists** — for that, see [`docs/FEATURES.md`](docs/FEATURES.md).

---

## 1. What this project is

Users upload documents, the documents are indexed, users chat with them, and answers carry citations.

That is the whole scope. This repository was deliberately started fresh from the `ai-shadow` prototype to keep it that way — see [`docs/DECISIONS.md`](docs/DECISIONS.md). The single most common way to damage it is to build toward the prototype's larger architecture (orchestrator, memory hierarchy, tool layer) instead of toward that sentence.

Before adding anything, ask: does upload → index → chat → citations need this? If not, it goes in `docs/ROADMAP.md`, not in the codebase.

---

## 2. Workflow

```
Understand → Plan → Discuss → Implement → Test → Review → Document → Commit
```

**Understand.** Check `docs/FEATURES.md` before touching a module, and read the surrounding code rather than inferring behaviour from names.

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
- **No unused code.** Ship it or leave it out. The three components currently carried without a caller are a recorded, time-boxed exception, not a precedent.
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

**A new prompt:** define it in `app/prompts/system.py`, add it to `register_default_prompts()`, and update `EXPECTED_PROMPT_NAMES` in `tests/prompts/test_system.py`. Never inline a prompt string in a service or route.

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

---

## 9. Security

- Secrets are read only through `app/config/settings.py`, never hardcoded, never logged.
- **Every stored resource is scoped to its owning user, and every query enforces that scope.** This holds now, with a placeholder owner, and must keep holding as authentication is introduced.
- There is no authentication yet. Treat that as a known limitation (`docs/KNOWN_ISSUES.md`), not as licence to add unscoped queries.

---

## 10. Documentation ownership

| Document | Owns |
|---|---|
| `docs/ARCHITECTURE.md` | System design |
| `docs/ROADMAP.md` | Phases and what is next |
| `docs/FEATURES.md` | What exists right now — authoritative |
| `docs/KNOWN_ISSUES.md` | Gaps, limitations, accepted tradeoffs |
| `docs/DECISIONS.md` | Why the codebase is shaped this way |
| `README.md` | Getting started and the API reference |
| `CLAUDE.md` | How we work |

Exactly one document owns each kind of fact. A change that alters implementation status, uncovers an issue, or makes a non-obvious call updates the matching document in the same change — not as a follow-up.
