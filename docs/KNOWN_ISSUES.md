# AI Shadow MVP — Known Issues

Current gaps, limitations and accepted tradeoffs. For what exists, see [`FEATURES.md`](FEATURES.md).

---

## Limitations

### No authentication or multi-user isolation
There is no auth layer. All ingestion runs as a single placeholder owner, `MVP_USER_ID`.
- **Impact:** the system is single-tenant and unauthenticated. Do not store production data in it.
- **Mitigation in place:** every table carries `user_id` and every query filters on it, so adding auth is a change to where that value comes from — not a migration or a backfill.
- **Priority:** High — before real user data.

### Ingestion is synchronous
Parsing, chunking and persistence all happen inside the upload request.
- **Impact:** a large document holds a request open for its whole processing time. There is no progress reporting beyond the final status.
- **Priority:** Medium — revisit when real document sizes make it a problem, per `DECISIONS.md`.

### Scanned documents are rejected, not OCR'd
A PDF with no text layer yields no extractable text and is recorded as `failed`.
- **Impact:** image-only PDFs cannot be ingested. The failure is explicit rather than silent, which is the intended behaviour, but it is still a gap.
- **Priority:** Low for the MVP; revisit if real uploads are frequently scanned.

### DOCX has no page numbers
Page boundaries in DOCX are a rendering property, so chunks from a DOCX carry a section heading but no page number.
- **Impact:** citations into DOCX documents will name a heading rather than a page.
- **Priority:** Low — accepted.

### Extraction quality is untested against real documents
`pypdf` handles well-formed PDFs. Multi-column layouts, tables and unusual encodings have not been exercised against anything but generated fixtures.
- **Impact:** unknown extraction quality on real customer material.
- **Priority:** Medium — the first thing to check with real uploads. If quality is poor, `unstructured` is the alternative to evaluate, at a significant dependency cost.

---

## Carried-forward decisions to revisit

### Three components have no caller
The LLM provider abstraction, prompt system and Analysis Engine were ported for the retrieval feature, which is not yet built.
- **Impact:** tested code that nothing exercises end to end.
- **Priority:** Medium — if retrieval is not built, remove them rather than leaving them indefinitely.

### `langchain-text-splitters` carries more weight than it earns
Used for one function, `RecursiveCharacterTextSplitter`, and pulls a transitive tree considerably larger than that.
- **Impact:** dependency surface out of proportion to the feature.
- **Priority:** Low — it is used in exactly one place and is straightforward to replace.

### OpenRouter embedding support is unverified
The retrieval phase assumes `client.embeddings.create()` works against OpenRouter's OpenAI-compatible embeddings endpoint. The endpoint is documented; official Python SDK compatibility is not explicitly stated.
- **Impact:** if it does not work, embeddings must call OpenAI directly, which changes the provider story for that one call.
- **Priority:** High for Phase 2 — time-box a check before building.

---

## Technical debt

### No CI pipeline
Nothing runs the test suite automatically.
- **Impact:** a broken change can be merged.
- **Note:** unlike the reference repository, the suite has no credential or service dependency, so adding CI is now unblocked — it needs `pip install -r requirements.txt` and `pytest`.
- **Priority:** Medium.

### No type checking in CI
`mypy` is not configured, though the codebase is fully annotated.
- **Priority:** Low.

### No structured logging
Nothing is logged. Ingestion failures are recorded on the document row, but there is no request or error log.
- **Priority:** Medium — before any deployment.

### No rate limiting on upload
`POST /documents/upload` accepts unbounded request volume; only per-file size is limited.
- **Priority:** Medium — before any public exposure.

---

## Resolved on migration from `ai-shadow`

These were open issues in the reference repository and were fixed while porting.

| Issue | Resolution |
|---|---|
| Misnamed and missing `__init__.py` files | Every package directory has a correct `__init__.py` |
| LLM client built at import, so tests needed live credentials | Client construction deferred to first use |
| `pytest` absent from `requirements.txt` | Declared, along with `python-multipart` |
| No `.env.example` | Added, with every variable documented |
| Hardcoded `echo=True` on the engine | Driven by `settings.DEBUG` |
| Settings with no defaults made the package unimportable | Every setting has a working default |
| `AnalysisValidationError` had no HTTP mapping | Exception handlers map all domain errors |
| `json.loads` failed on markdown-fenced model output | Fences stripped before parsing |
| `CHROMA_PATH` configured with no implementation | Removed |
| Three never-raised exception classes | Not carried forward |
| Four prompt templates with no caller | Not carried forward |
| Five empty service stub files | Not carried forward |
| No linter or formatter config | `ruff` configured for both |
