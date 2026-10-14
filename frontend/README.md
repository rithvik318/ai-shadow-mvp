# SunRadia — Digital Twin Platform (frontend)

The React workspace for AI Shadow. Six screens over the existing API — **Chat**,
**Digital Twins**, **Knowledge Base**, **Email Agent**, **Tasks** and
**Reports**. It holds no server state of its own beyond conversation history,
which lives in the browser because the chat endpoint is stateless.

The user guide, architecture and deployment instructions are in the
[root README](../README.md).

## Running it

Start the backend first (`uvicorn app.main:app --reload` from `backend/`, port
8000), then:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with the `/api` proxy |
| `npm run build` | Typecheck, then production build into `dist/` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Node test runner over `src/tests/` (jsdom; no browser or bundler) |
| `npm run lint` | ESLint over `src` |
| `npm run format:check` | Prettier |

## Configuration and the API proxy

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | Where the browser sends API calls |
| `VITE_BACKEND_ORIGIN` | `http://localhost:8000` | Where the dev proxy forwards `/api` |

The backend registers no CORS middleware, so every call must be same-origin:

- **Development:** Vite proxies `/api/*` to `VITE_BACKEND_ORIGIN`, stripping the
  prefix.
- **Production:** `vercel.json` rewrites `/api/*` to the Render backend and adds
  the `x-origin-secret` header from Vercel's `ORIGIN_SECRET`.

Leave `VITE_API_BASE_URL` unset in both cases.

## Identity

The **Current Digital Twin** selector stores the chosen twin's id in
`localStorage` (`sunradia.currentTwinId`) and sends it as `X-User-ID` on
user-scoped calls. Shared-corpus calls (`/documents`, `/search`, `/sync`) send
no identity. This is identity, not sign-in — the backend trusts the header as
sent, and the sidebar says so.

## What "Preferences" means here

The backend has no preferences table. The Preferences tab edits exactly two real
things: `communication_style` and `decision_preferences` on the profile, and
lists memories whose `type` is `preference`. `src/lib/twin.ts` is the single
place that states this.

## Layout

```
src/
  api/         client.ts (fetch, X-User-ID, ApiError), index.ts (one function per endpoint), types.ts
  components/  chat/, twins/, knowledge/, email/, tasks/, reports/, ui/, Sidebar.tsx
  lib/         pure logic — conversations, uploads, twin, knowledge, email, tasks, reports, formatting
  state/       TwinContext — which twin the app is acting as
  tests/       node:test suites over api/, lib/ and selected panels
```

`src/api/types.ts` mirrors `backend/app/schemas/`; a field that does not exist
there does not exist here.

## Known limitations

- **Chat history is browser-local** (`localStorage`), not synchronised between
  machines, and each question is answered without earlier messages as context.
- **Twin card counts cost one call per twin**; fine for a handful of twins.
- **Upload progress is per batch**, because `POST /documents/batch-upload` is a
  single synchronous request.
- **`DigestView.tsx` is not mounted anywhere.** The Reports screen renders the
  detailed report with `ActivityReportDocument` and a stored digest with its own
  `ActivityDocument` component.
