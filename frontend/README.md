# SunRadia — Digital Twin Platform (frontend)

Three first-class areas over the existing backend: **Chat**, **Digital Twins**
and **Knowledge Base**. It adds no state of its own beyond conversation history,
which lives in the browser because the chat endpoint is stateless. Every
document, profile, memory, answer and sync figure comes from the API.

## Running it

The backend must be running first (`uvicorn app.main:app --reload` from
`backend/`, on port 8000).

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

`npm run dev` proxies `/api` to `http://localhost:8000`. Point it elsewhere with
`VITE_BACKEND_ORIGIN`.

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with the API proxy |
| `npm run build` | Typecheck, then production build into `dist/` |
| `npm run typecheck` | `tsc --noEmit` over everything |
| `npm run test` | Pure-logic tests, no browser or bundler needed |
| `npm run lint` | ESLint over `src` |
| `npm run format:check` | Prettier |

## Why there is a proxy

The backend registers no CORS middleware, so a browser refuses a direct call
from `:5173` to `:8000`. The dev proxy makes every request same-origin, which
needs no backend change. **In production, serve `dist/` from the same origin as
the API** (or behind one reverse proxy). Pointing `VITE_API_BASE_URL` straight
at a different origin will fail in the browser until CORS is added server-side.

## Backend endpoints used

| Endpoint | Used by |
|---|---|
| `GET /users`, `POST /users` | User selector |
| `POST /chat` | Chat — sends `X-User-ID` |
| `POST /documents/batch-upload` | Upload tray and multi-file upload |
| `GET /documents`, `DELETE /documents/{id}` | Knowledge base panel |
| `GET /profile`, `PUT /profile` | Profile and Preferences tabs — `X-User-ID` |
| `GET /memory`, `POST /memory`, `PATCH /memory/{id}`, `DELETE /memory/{id}` | Memories tab — `X-User-ID` |
| `GET /sync/onedrive/status`, `POST /sync/onedrive` | Knowledge sources panel |

`POST /search` is wrapped in `src/api/index.ts` but has no screen yet — it is a
retrieval-tuning surface rather than a product one.

## What "Preferences" means here

The backend has **no preferences table and no preferences endpoint**. Preferences
are two real things, and the Preferences tab edits exactly those:

1. `communication_style` and `decision_preferences` on the profile
2. memories whose `type` is `preference`

`src/lib/twin.ts` is the single place that states this, and the profile form
exposes only fields `ProfileRequest` accepts — there is no `bio`.

## Known limitations

**Chat history is browser-local.** The backend's chat endpoint is stateless — it
stores no conversation and consults none — so each question is answered from the
documents alone. Conversations are kept in `localStorage` so the sidebar is
useful across reloads; they are not synchronised between machines, and no
backend API is implied.

**The user selector is not authentication.** The backend trusts `X-User-ID`
exactly as it is sent. Choosing a user selects whose Digital Twin shapes an
answer; it grants nothing. The sidebar says so.

**OneDrive is not configured.** The sync panel reads real state from
`GET /sync/onedrive/status` and shows "Not configured" until SunRadia supplies
the Microsoft Graph tenant, credentials and folder list. No credential flow
lives in the frontend, and no OneDrive data is simulated.

**Twin card counts cost one call per twin.** There is no aggregate endpoint, so
memory and preference counts are loaded per twin from its own scoped calls. Fine
for a handful of twins; it would need an endpoint at larger scale.

**Upload progress is per batch, not per byte.** `POST /documents/batch-upload`
is a single synchronous request that returns when every file is done, so the
tray shows each file as uploading and then its outcome. A per-file percentage
would need a streaming or job-based endpoint that does not exist.

## Layout

```
src/
  api/         client.ts (fetch, X-User-ID, errors), index.ts (one fn per endpoint), types.ts
  lib/         pure helpers — upload result mapping, formatting
  state/       UserContext — who the app is acting as
  components/  ui/ primitives, chat/, documents/, twin/, Sidebar
  tests/       node:test suites over api/ and lib/
```

`src/api/types.ts` mirrors `backend/app/schemas/`. A field that does not exist
there does not exist here.
