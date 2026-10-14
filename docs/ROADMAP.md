# AI Shadow MVP — Roadmap

The MVP is one sentence: **users upload documents, the documents are indexed,
users chat with them, and answers carry citations** — with a Digital Twin
shaping how answers and emails are written. Where the project stands is in
[`PROJECT_STATE.md`](PROJECT_STATE.md); what exists is in
[`FEATURES.md`](FEATURES.md).

---

## Current phase

**Deployed MVP — hardening.** All MVP features are built and the application is
live (Vercel → Render → Neon). The remaining work is operational completion and
hardening, not new features.

## Delivered

- Document ingestion (PDF, DOCX, PPTX, XLSX, HTML, TXT, Markdown) with
  provenance, content-hash identity, batch upload and embedding backfill.
- Semantic retrieval over pgvector, retrieval-only search, and grounded chat with
  source references and intent routing for greetings and twin questions.
- Multi-user Digital Twins with profiles and typed memories.
- OneDrive / SharePoint synchronisation with delta runs, source resolution,
  status reporting and an optional schedule.
- Email Agent: per-user Outlook mailbox, triage, compose and revision
  operations, templates, drafts with attachments, approval-gated sending,
  follow-ups.
- Tasks with derived urgency and escalation; follow-ups converted to tasks.
- Weekly and monthly activity reports (stored, evidence-gated, with structural
  fallback), email digests with `.docx` download, and the weekly work view.
- Manual calendar import and attendance recording.
- React workspace covering every feature above.
- Production deployment with an origin-secret perimeter.

## Next — operational completion

Configuration and administrator actions; no code change expected.

1. **Grant `Calendars.Read`** to the Entra application, then run
   `POST /calendar/import` for one window to exercise the live calendar mapping.
2. **Run a complete OneDrive resync** with the corrected sync code
   (`POST /sync/onedrive` with `full: true`), then check
   `GET /sync/onedrive/status` and `python -m scripts.validate_live_integration --full`.
   Ensure production's Knowledge Base is populated.
3. **Verify sending** with one approved draft from a test mailbox, confirming
   `Mail.Send` consent end to end.
4. **Delete collected report data** (`backend/report_data*/`) once the reports
   it fed are final. It is ignored by git but holds real correspondence.

## Then — hardening

In rough priority order:

1. **Authentication** behind the existing `X-User-ID` identity (a session or
   token replacing the trusted header; no data migration needed).
2. **CI** running `pytest`, `ruff` and the frontend checks.
3. **Tune `RETRIEVAL_SIMILARITY_THRESHOLD`** against the real corpus using
   `POST /search` (default `0.0` is permissive).
4. **Background ingestion and sync**, so large uploads and first syncs do not
   hold a request open.
5. **Inbox cursor paging** (a provider-contract change) once mailboxes exceed the
   200-message page.
6. **Sentence-level citations** via index-based citing validated by the
   Analysis Engine.

## Deliberately deferred

- **Autonomous behaviour.** No scheduled or automatic sends, automatic replies,
  background triage sweeps, automatic calendar import, or anything that
  contacts a person without explicit approval.
- **Reference-repository architecture** — the AI Orchestrator,
  conversation/task memory system, tool layer and multi-agent workflows — and
  CRM, LangGraph and n8n. Not built until something concretely requires them.
- **Additional mail providers.** Gmail would be one module beside
  `outlook_provider.py` plus a registry entry.

### Optical character recognition for scanned documents

Documents in a supported format with no text layer are recorded
`ocr_required` (tagged `OCR_REQUIRED / NO_TEXT_LAYER`), so the backlog is a
query; `scripts/diagnose_onedrive_corpus.py` lists them by name. About 30 such
files were found in the SunRadia corpus. Any OCR implementation belongs behind
the existing parser boundary (bytes in, `ParsedSection`s out) and is out of
scope until the normal retrieval pipeline is accepted.
