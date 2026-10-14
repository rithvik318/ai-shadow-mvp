# AI Shadow MVP — Project State

A checkpoint, not a history. Read this first in a new session. What exists, in
detail, is [`FEATURES.md`](FEATURES.md); open gaps are in
[`KNOWN_ISSUES.md`](KNOWN_ISSUES.md); what comes next is in
[`ROADMAP.md`](ROADMAP.md); how work gets done is [`../CLAUDE.md`](../CLAUDE.md).

**Updated:** 2026-10-14.

---

## Where the project stands

**Deployed MVP.** The application is live at
<https://ai-shadow-mvp.vercel.app/>:

| Tier | Platform | Configuration |
|---|---|---|
| Frontend | Vercel | `frontend/vercel.json` — static SPA; `/api/*` rewritten to the backend with an `x-origin-secret` header |
| Backend | Render | `render.yaml` — `ai-shadow-api`, `alembic upgrade head` as the pre-deploy step, health check `/health` |
| Database | Neon PostgreSQL + pgvector | Supplied as `DATABASE_URL` (the Blueprint also declares a Render database; production points `DATABASE_URL` at Neon) |
| Models | OpenRouter | `LLM_PROVIDER=openrouter`; completions `openai/gpt-oss-20b` (`:nitro` in `render.yaml`), embeddings `openai/text-embedding-3-small` |

**Feature-complete for the MVP scope**, backend and frontend: grounded chat with
citations, multi-user Digital Twins (profile and memory), document ingestion and
OneDrive synchronisation, the Email Agent (triage, drafting, templates, approval
and sending), tasks and follow-ups, weekly/monthly activity reports, and manual
calendar import. Nothing is in progress.

Most recent changes:

- **Chat intent routing.** Greetings and questions about the twin are answered
  from the profile and memories (`twin_conversation` prompt) instead of
  returning the knowledge-base no-match reply; responses carry `answer_basis`.
- **Provider errors are 502, not 500.** `LLMService` wraps OpenAI SDK errors as
  `LLMServiceError`, logged by class and status only.
- **Output-token cap.** Every completion sends `max_tokens=4096`
  (`MAX_OUTPUT_TOKENS` in `llm_service.py`) to bound OpenRouter's per-request
  reservation.
- **Deployment perimeter.** `ORIGIN_SECRET` middleware, the Vercel rewrite, and
  `psycopg[binary]` for Render's `postgresql://` URLs.

## Verification status of external integrations

The test suite runs offline against mocks. What has and has not been exercised
against the real SunRadia tenant and providers:

| Integration | Live status |
|---|---|
| OpenRouter completions and embeddings | Working in production |
| Microsoft Graph authentication | Working |
| Mail **read** (`Mail.Read`) | Exercised live — activity reports and the September/October 2026 report data collection read real mailboxes |
| Mail **send** (`Mail.Send`) | Not recorded as exercised from this repository |
| OneDrive synchronisation | Source resolves and enumerates. One early live run ended `partial` (847 files) and exposed defects that are now fixed; a complete `--full` resync with the corrected code is not recorded |
| Calendar (`Calendars.Read`) | **Not granted** — Graph returns `403 ErrorAccessDenied` |

The production Knowledge Base holds whatever has been uploaded or synchronised
into the production database; in mid-October 2026 it was observed empty.

## Test status

Measured on 2026-10-14 from the working tree:

- **Backend:** `pytest -q` → 1,719 passed, 12 skipped (live-PostgreSQL tests
  skip without a database). Offline, SQLite, no credentials.
- **Frontend:** `npm test` → 243 passed.

There is no CI; these numbers come from manual runs.

## Architecture boundaries

These are the rules the code is built around. Breaking one is a design change,
not a refactor.

- **The company knowledge base is shared.** `documents.user_id` and
  `document_chunks.user_id` hold `MVP_USER_ID` for everyone. Document, search
  and sync routes take no identity.
- **The Digital Twin is private.** Profile, memory, mailbox, assessments,
  drafts, templates, tasks, events and reports are UUID foreign keys into
  `users`, and every query filters on them.
- **Persona shapes, documents cite.** Profile and memory influence tone,
  emphasis and priorities. Only retrieved passages may be cited as `[SOURCE n]`,
  and `sources` is built from retrieved chunks alone.
- **`X-User-ID` is identity, not authentication.** Missing → `401`, malformed →
  `422`, unknown → `404`. It never creates a user and is trusted as sent.
  `ORIGIN_SECRET` only proves a request came through the proxy.
- **Layering holds.** API → feature services → engines → LLM layer →
  persistence. Services raise domain errors from `app/core/exceptions.py`; only
  `app/main.py` knows status codes. Prompts are registered templates.
- **One ingestion pipeline.** Single upload, batch upload and OneDrive sync all
  call `ingestion_service`; one retriever serves `/search`, `/chat` and email.
- **One mailbox per person, resolved from the caller.**
  `mailbox_config_service` builds a provider for a user from `user_mailbox`.
  `EMAIL_MAILBOX_ADDRESS` is a fallback that is off unless
  `EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX` is set.
- **The Email Agent never sends on its own.** A draft leaves only after
  `POST /email/drafts/{id}/approve` and then `/send` with `confirm: true`; any
  edit withdraws approval. No prompt, service or route gives a model a path to a
  provider, and there is no simulated mailbox.
- **A mailbox is not mirrored.** Only triage conclusions plus subject, sender
  and timestamp are stored; message bodies are not.
- **Derived states are never written back.** Overdue, urgency, escalation, task
  colour and an unanswered meeting's `unknown` are computed on read.
- **Every user-owned table joins `_OWNED_MODELS`**, so deleting a user removes
  everything they own and nothing shared.
- **Vendor knowledge stays at the edges.** Only `app/services/llm/client.py`
  names an LLM provider; only `app/services/graph/` knows Graph; only
  `app/services/email/provider/` knows Outlook.
- **No folder is named in code.** `ONEDRIVE_SOURCES` is the only place a
  OneDrive folder appears; Graph ids come from Graph, never decoded from URLs.
- **One sync per source at a time**, claimed in `onedrive_sync_state` (stale
  after six hours). The delta token advances only when that is safe.
- **Only `indexed` documents are retrievable.**
- **Email timestamps are timezone-aware on every dialect** (`UtcDateTime` in
  `app/database/types.py`).
- **The frontend holds no server state.** Conversations live in browser
  `localStorage` because `/chat` is stateless; everything else comes from the
  API.

## Assumptions recorded where the brief was ambiguous

Each is a configurable default or a contained choice.

- **`overdue` and `escalation_required` are display states**, computed into
  `display_status`; `todo`, `in_progress`, `completed`, `blocked` and
  `cancelled` are stored.
- **Escalation outranks overdue** in `display_status`.
- **Task warning and escalation thresholds** are `TASK_WARNING_DAYS` (3) and
  `TASK_ESCALATION_DAYS` (7).
- **The escalation target comes from configuration only**
  (`ESCALATION_CONTACT_ADDRESS`); without it the report says "Escalation target
  not identified".
- **A past meeting nobody has answered for reads `unknown`.**
- **Recipient display names are not stored**; `"Name <addr>"` becomes `addr`.
- **The weekly work view looks seven days back and seven forward** for meetings.
- **Deleting a twin requires typing its name**, and an administrator.
- **Report periods are UTC**, weeks starting Monday.
