# AI Shadow — Digital Twin MVP

A working assistant for a small team. Each person has a **Digital Twin** — a
profile and a private memory store that shape how the system writes as them —
sitting over a **shared company knowledge base** built from documents and
synchronised OneDrive/SharePoint folders.

From there it does three things, and keeps them separate on purpose:

- **Email** — what came in, and what needs action.
- **Tasks** — what you need to do.
- **Reports** — what happened this week, and this month.

A FastAPI backend over PostgreSQL with pgvector, and a React workspace in front
of it.

---

## Quick start

```bash
# 1. Postgres with the pgvector extension
docker compose up -d

# 2. Backend
cd backend
python -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env            # defaults match docker-compose
alembic upgrade head
python -m scripts.verify_embedding_provider
uvicorn app.main:app --reload

# 3. Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

The workspace runs at `http://localhost:5173` and proxies `/api` to the backend
at `http://localhost:8000`. Interactive API documentation is at
`http://localhost:8000/docs`.

The embedding check makes one live call and prints the vector width it got
back. If it fails, set `EMBEDDING_PROVIDER=openai` and
`EMBEDDING_MODEL=text-embedding-3-small` in `.env` — no code change is needed.

Nothing above requires Microsoft credentials. A deployment with none serves
normally: documents can be uploaded by hand, and the Email Agent still composes,
rewrites, saves drafts and manages templates. What needs a mailbox is reading an
inbox, sending, and the email half of the Activity Reports.

Run the backend tests with `pytest` from `backend/`, and the frontend's with
`npm run test`. Neither needs a running service or any credentials.

---

## What it does

### Digital Twins, and what is shared

Each user has a profile and a typed memory store — facts, preferences,
decisions, commitments, context — which are **private**, enforced by a foreign
key rather than a convention. The document corpus is **shared**: everyone
searches the same company knowledge, and one person's Twin never becomes a
citable source for another's answer. Switching the selected Twin in the sidebar
switches every user-scoped screen at once.

Identity is carried by an `X-User-ID` header. It is a development mechanism,
not authentication — see [Security](#security).

### Knowledge base and grounded chat

Documents arrive by upload (PDF, DOCX, TXT, Markdown) or from configured
OneDrive/SharePoint folders. They are parsed with their page numbers and
headings intact, chunked with overlap, embedded, and stored in pgvector.

`POST /chat` answers only from retrieved passages and cites them; where
retrieval finds nothing the prompt says so and forbids any company claim, so
"I don't know" is a real answer rather than an invented one. `POST /search`
returns the same ranked passages without calling the model.

### OneDrive / SharePoint synchronisation

Incremental, using Microsoft Graph delta tokens: new files are ingested,
modified files replace their previous chunks, deleted files are removed, and
unchanged files are skipped. Each source keeps its own delta token, so a source
can be paused and resumed without re-indexing. Configuration lives entirely in
`ONEDRIVE_SOURCES` — no folder is named anywhere in application code. See
[Setting up OneDrive](#setting-up-onedrive-synchronisation).

### Email Agent

Per-user mailboxes, connected from the UI. Composing, replying, rewriting,
shortening, changing tone and drafting from templates all run through the
Digital Twin's persona and can be grounded in the knowledge base.

**Triage** classifies a message as urgent, needs-reply, FYI, follow-up or
low-priority, with a summary, a suggested action and a follow-up recommendation.
A due date appears only where the message itself gave one. Assessments are
stored; message bodies are not.

**The assistant cannot send.** A draft leaves only after an explicit approval
followed by an explicit send, and editing a draft withdraws its approval — so an
approved draft is always the text somebody read.

### Follow-ups → Tasks

Emails triage judged actionable appear under Follow-ups with an **Add to
Tasks** button. One press creates a task carrying the triage summary, the
sender and the stated deadline, and the row then reads *✓ Task created*.
Pressing it again returns the same task: deduplication is keyed on the message,
so a follow-up cannot become two tasks.

Tasks can also be created by hand. Both routes produce the same task with the
same lifecycle — there is deliberately no second kind of task and no second
list.

### Tasks

`todo → in_progress → completed`, with `blocked` and `cancelled` alongside.
Completion is nearly terminal: a finished task can be deliberately reopened to
*to do*, but it cannot be *started*, so "done" cannot drift back through a
misclick.

What a person sees is simpler — four colours, decided on the server from
completion and the deadline alone:

| Colour | Meaning |
|---|---|
| Grey | To do — not yet due, or fewer than two days past |
| Amber | Attention — two days overdue |
| Red | Urgent — more than two days overdue |
| Green | Done |

Lateness is counted in whole days, so the amber band is the whole of the third
day rather than an instant. A task with no deadline stays grey: nothing was
promised, and no date is ever invented to have something to colour.

### Reports

Two documents — a **Weekly Activity Report** and a **Monthly Activity Report**
— in eight sections: executive summary, major workstreams, important
conversations, decisions and outcomes, requires follow-up, completed work, risks
and issues, and the bottom line. Each is assembled from real email and task
activity and downloads as a Word document.

**Nothing in a report is generated prose.** Every line comes from a count or a
row, sections with nothing in them are omitted rather than padded, and a period
with no mailbox still produces a report — the task half is real, and the email
half says it was unavailable.

A report is snapshotted when generated and never rewritten, so completing a task
in September cannot change August's report. History lists only what was actually
generated, capped at the five most recent.

A separate interactive weekly work view covers meetings that need an answer and
escalations, which are actions on live work rather than a document.

### Calendar

Events are user-scoped, and attendance is recorded only when a person says what
happened — a meeting existing is never treated as evidence that anybody went to
it. Imported calendar data requires a Graph permission this deployment does not
yet have; until then events are entered by hand and the reports say the calendar
was unavailable rather than showing an empty week.

---

## Architecture

```
React workspace (Vite, TypeScript, Tailwind)
        │  /api
FastAPI  ├── api/          routes; the only layer that knows HTTP status codes
         ├── schemas/      Pydantic request and response models
         └── services/
             ├── features/     documents, retrieval, chat, digital_twin,
             │                 email, tasks, calendar, reports, sync, users
             ├── engines/      reusable, domain-agnostic AI capabilities
             ├── email/        mailbox provider boundary (Outlook)
             ├── graph/        Microsoft Graph client and drive service
             └── llm/          provider abstraction and embeddings
PostgreSQL + pgvector, migrated with Alembic
```

Four boundaries hold the design together. Business logic never names an LLM or
embedding vendor. The mailbox is a Protocol with no vendor word above it, and no
provider may report a send it did not make. Services raise domain errors and
only `app/main.py` maps them to status codes. And derived facts — overdue,
urgency, escalation, a task's colour — are computed on read rather than stored,
because a stored "overdue" is wrong the moment the clock moves.

---

## API

Full request and response detail is at `/docs`. Every user-scoped endpoint takes
the `X-User-ID` header.

| Area | Endpoints |
|---|---|
| Documents | `POST /documents/upload`, `POST /documents/batch-upload`, `GET /documents`, `GET /documents/stats`, `GET/DELETE /documents/{id}` |
| Chat and search | `POST /chat`, `POST /search` |
| Digital Twin | `GET/PUT /profile`, `GET/POST /memory`, `PATCH/DELETE /memory/{id}` |
| Users | `GET/POST /users`, `GET /users/{id}/deletion-preview`, `DELETE /users/{id}` |
| Sync | `POST /sync/onedrive`, `GET /sync/onedrive/status` |
| Mailbox | `GET/PUT/DELETE /email/mailbox`, `GET /email/provider/status` |
| Email | `POST /email/compose`, `GET /email/messages`, `POST /email/triage`, `POST /email/threads/summarize`, `GET /email/assessments`, `GET /email/follow-ups`, `POST /email/follow-ups/{id}/handled` |
| Drafts and templates | `GET/POST /email/drafts`, `GET/PATCH/DELETE /email/drafts/{id}`, `POST /email/drafts/{id}/approve`, `POST /email/drafts/{id}/send`, attachments, and the `/email/templates` set |
| Tasks | `GET/POST /tasks`, `GET/PATCH/DELETE /tasks/{id}`, `POST /tasks/{id}/complete`, `POST /tasks/from-follow-up/{assessment_id}`, `POST /tasks/from-follow-ups` |
| Calendar | `GET/POST /events`, `GET/PATCH/DELETE /events/{id}`, `POST /events/{id}/attendance` |
| Reports | `GET /reports`, `GET /reports/history`, `GET /reports/document`, `POST /reports/digests/run`, `GET /reports/weekly` |
| Service | `GET /health`, `GET /` |

Every mapped domain error returns the same shape:

```json
{ "detail": "Unsupported document type: application/zip. Supported formats are PDF, DOCX, TXT and Markdown.", "error": "UnsupportedDocumentTypeError" }
```

---

## Configuration

Set in `backend/.env`; see [`.env.example`](.env.example) for the full list with
defaults and the reasoning behind each. Every setting has a working default, so
the application and its tests import without a `.env` present.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local docker-compose Postgres | Connection string |
| `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | `openai/text-embedding-3-small`, `1536` | Must agree; the column is created at this width |
| `EMBEDDING_PROVIDER` | unset | Unset means "same as `LLM_PROVIDER`" |
| `LLM_PROVIDER`, `LLM_MODEL` | `openrouter`, `openai/gpt-oss-20b` | Completions only; not used by ingestion |
| `RETRIEVAL_TOP_K`, `RETRIEVAL_SIMILARITY_THRESHOLD` | `5`, `0.0` | Chunks per search, and the cosine floor |
| `ONEDRIVE_*` | unset | Graph credentials, drive and sources — see below |
| `EMAIL_PROVIDER`, `EMAIL_INBOX_PAGE_SIZE` | unset, `50` | `outlook`, or empty for no mailbox |
| `TASK_WARNING_DAYS`, `TASK_ESCALATION_DAYS` | `3`, `7` | Urgency and escalation in the weekly work report. The Tasks page's four colours are a separate, fixed rule |
| `ESCALATION_CONTACT_ADDRESS` | unset | Who unresolved work escalates to. Unset reads "Escalation target not identified" rather than guessing |
| `REPORT_DIGEST_SCHEDULE_ENABLED` | `false` | Snapshot each user's last completed week and month on a timer |
| `BOOTSTRAP_ADMIN_EMAIL` | unset | Who becomes administrator when nobody is. Unset promotes the earliest-created user |

---

## Setting up OneDrive synchronisation

Not required to run the application; a deployment with no credentials serves
normally and reports `configured: false`.

**1. Register an application** in Microsoft Entra ID. Note the *Application
(client) ID* and *Directory (tenant) ID*.

**2. Grant application permissions.** Microsoft Graph → **Application**
permissions (not Delegated) → `Files.Read.All`, then **Grant admin consent** —
without it every Graph call returns `403`.

`Files.Read.All` is the whole requirement for reading files: as an application
permission it covers all site collections, including SharePoint libraries and
every user's OneDrive for Business. `Sites.Read.All` governs the `/sites`
discovery endpoints, which this application does not call — granting it will not
fix a `403` on a drive.

For the Email Agent, that same application additionally needs `Mail.Read` and
`Mail.Send`, which are a **separate consent** from files.

**3. Create a client secret** and copy the value immediately; it is shown once.
It is never logged, never returned by any endpoint, and must never be committed.

**4. Find the drive id**, without needing a Graph Explorer session:

```bash
cd backend
python -m scripts.discover_onedrive_sources --user someone@example.com
python -m scripts.discover_onedrive_sources --drive <drive-id>    # top-level folders
python -m scripts.verify_graph_access                             # what the token can reach
```

**5. Fill in `.env`** — `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`,
`ONEDRIVE_CLIENT_SECRET`, `ONEDRIVE_DRIVE_ID`.

**6. Configure the folders.** `ONEDRIVE_SOURCES` is a JSON array on one line,
and is the only place a folder is named:

```
ONEDRIVE_SOURCES=[{"key":"capabilities","label":"Capabilities","path":"Documents/Capabilities"}]
```

| Field | Required | Notes |
|---|---|---|
| `key` | yes | Stable identity. The delta token is stored against it, so renaming one forces a full resync |
| `label` | no | What a person sees. Defaults to the path |
| `drive_id` | no | Falls back to `ONEDRIVE_DRIVE_ID` |
| `path` | one of | Folder path from the drive root |
| `item_id` | one of | The folder's Graph id. Preferred once known — it survives renames |
| `share_url` | one of | A sharing link, resolved by Graph. For a folder whose drive nobody knows |
| `uri` | no | Human-facing link for status screens. Never used to address Graph |
| `enabled` | no | `false` pauses a source without deleting it; its delta token survives |

Pin paths to ids once they are known:

```bash
python -m scripts.discover_onedrive_sources --resolve
```

**A sharing link only resolves inside the tenant.** A `1drv.ms` or
`onedrive.live.com` link points at consumer OneDrive, owned by a personal
Microsoft account rather than by the tenant — an application token has authority
only inside the tenant that issued it, so no permission grant can read it. Such
content has to be copied into the tenant and linked from its
`*.sharepoint.com` address.

---

## Security

`X-User-ID` is a **development identity mechanism, not authentication**: it is
trusted exactly as sent, so anyone who can reach the API can act as any user. It
exists so the multi-user boundary could be built and relied on now, and so that
adding real authentication later changes only where that value comes from —
every service below already takes the user it operates on as an argument.

Within that limitation the boundaries are real and enforced:

- **Private per user** — profile, memories, mailbox, triage assessments, drafts,
  templates, tasks, calendar events and reports. Each is a foreign key into
  `users` with a cascade, and every query filters on it.
- **Shared** — the document corpus and its chunks. Deleting a user removes
  everything they own and, by an explicit check inside the same transaction,
  nothing of the company's.
- **The assistant never sends.** Approval and sending are separate explicit
  acts, and there is no in-memory or demo mailbox provider, so a "Sent" badge
  always means a provider confirmed it.
- Secrets are read only through `app/config/settings.py`, never logged, never
  returned by an endpoint.

Do not put production data in this system until authentication exists. See
[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) | Where the project stands right now — read first |
| [`docs/FEATURES.md`](docs/FEATURES.md) | What exists today — authoritative |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design |
| [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) | Current gaps and limitations |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phases and what is next |
| [`CLAUDE.md`](CLAUDE.md) | How work gets done here: process, standards, patterns |
