# AI Shadow — Digital Twin MVP

> **Live application: [https://ai-shadow-mvp.vercel.app/](https://ai-shadow-mvp.vercel.app/)**

AI Shadow is SunRadia's AI-powered **Digital Twin** platform. It lets each team
member ask questions of the company's shared Knowledge Base and get answers
that cite the documents they came from. It also lets them set up a personal
Digital Twin, a profile plus a memory, that shapes how answers and emails are
written. The same workspace drafts and triages email, tracks follow-ups and
tasks, and produces weekly and monthly activity reports.

**At a glance**

| Area | What you can do |
|---|---|
| **Chat** | Ask questions about company documents and get answers with source references; greet your twin or ask it to introduce itself |
| **Digital Twins** | Create a twin for a person and give it a role, priorities, preferences and memories |
| **Knowledge Base** | Upload documents, watch them being indexed, see OneDrive synchronisation status |
| **Email Agent** | Triage a connected Outlook mailbox, draft replies and new emails, use templates, approve and send |
| **Tasks** | Track work by hand or from email follow-ups, with overdue and urgency signals |
| **Reports** | Weekly and monthly activity reports from mailbox and task activity, downloadable as Word documents |

> **Read before using with real data.** AI Shadow is an MVP. It has **no
> sign-in**: choosing a Digital Twin selects *whose* data you are working with,
> but it does not prove who you are. See
> [Security, privacy and limitations](#13-security-privacy-and-limitations).

---

## Table of contents

**For everyone**

1. [What is AI Shadow?](#1-what-is-ai-shadow)
2. [Getting started and the interface](#2-getting-started-and-the-interface)
3. [Digital Twins — setup and configuration](#3-digital-twins--setup-and-configuration)
4. [Chat — user guide](#4-chat--user-guide)
5. [Knowledge Base — documents and OneDrive](#5-knowledge-base--documents-and-onedrive)
6. [Email Agent — full workflow](#6-email-agent--full-workflow)
7. [Tasks](#7-tasks)
8. [Reports](#8-reports)

**For developers and administrators**

9. [How the platform works](#9-how-the-platform-works)
10. [Technology stack](#10-technology-stack)
11. [Installation and local development](#11-installation-and-local-development)
12. [Deployment guide](#12-deployment-guide)
13. [Security, privacy and limitations](#13-security-privacy-and-limitations)
14. [Troubleshooting and FAQ](#14-troubleshooting-and-faq)
15. [Repository structure and contributing](#15-repository-structure-and-contributing)

---

## 1. What is AI Shadow?

### The problem it solves

Company knowledge sits in many places: capability statements, proposals,
technical write-ups, contracts and a constant stream of email. Finding the right
passage, answering a client in the right voice, and remembering what needs
following up all take time. AI Shadow puts these in one workspace:

- **Ask, don't search.** Ask a question in plain language and get an answer
  written from the company's own documents, with the passages it used listed
  underneath.
- **Answers in your voice.** Your Digital Twin knows your role, your priorities
  and the way you like to communicate, and answers and drafts are written
  accordingly.
- **Email with a safety catch.** The assistant reads, sorts and drafts email,
  but nothing is sent until a person approves the draft and then presses
  **Send**.
- **A record of the work.** Weekly and monthly activity reports summarise what
  happened in your mailbox and task list.

**Intended users:** SunRadia staff, such as business development, recruiting,
delivery and management, who answer questions about company capabilities,
correspond with clients and partners, and report on their work.

### What a "Digital Twin" means here

In AI Shadow, a **Digital Twin is a person the assistant answers for**. It
consists of:

| Part | What it holds | Example |
|---|---|---|
| **Identity** | Name, email, role | "Jane Doe · Business Development Lead" |
| **Profile** | Organization, communication style, responsibilities, expertise, priorities, decision preferences, current focus | "Concise and executive-friendly"; priority "Federal opportunities" |
| **Memories** | Short, durable notes of type *fact*, *preference*, *decision*, *commitment* or *context*, each with an importance from 1 to 5 | "Prefers bullet-point summaries" |

A Digital Twin is **not** a separate AI model, and it has no document store of
its own. Every twin uses the same language model and the same shared Knowledge
Base. The twin's profile and memories are added to the instructions the model
receives, so they change **how** an answer is written (tone, emphasis, which
options are raised). They do not change **what the documents say**.

### Shared Knowledge Base vs. personal Digital Twin

| | Shared Knowledge Base | Personal Digital Twin |
|---|---|---|
| **Contents** | Company documents (uploaded or synchronised from OneDrive) | One person's profile, memories, mailbox link, drafts, templates, tasks and reports |
| **Who sees it** | Everyone; all twins search the same documents | Only that twin's data is loaded when that twin is selected |
| **Used as evidence?** | **Yes.** Answers cite these passages | **No.** Profile and memories shape style but are never cited as sources |

### How an answer is produced

1. You type a question in **Chat**.
2. AI Shadow decides whether it is a greeting, a question about the twin
   itself, or a question about the documents.
3. For a document question, it searches the Knowledge Base for the most
   relevant passages (semantic search over embeddings).
4. It sends those passages, plus your twin's profile and memories, to the
   language model with strict instructions: answer only from the passages and
   cite them.
5. The answer appears with a **Sources** list showing the passages that were
   given to the model.

If nothing relevant is found, AI Shadow says so instead of guessing.

### What the MVP supports, and what it does not

| Supported now | Not supported / not yet |
|---|---|
| Grounded chat over shared documents, with source references | Sign-in, passwords or single sign-on |
| Digital Twin profiles and memories | Memories learned automatically from chat (all memories are added by hand) |
| Upload of PDF, DOCX, PPTX, XLSX/XLSM, HTML, TXT, Markdown | OCR for scanned PDFs or images; legacy `.doc`, `.xls`, `.ppt` |
| OneDrive / SharePoint synchronisation, configured by an administrator on the server | Configuring OneDrive sources from the UI |
| Outlook mailbox triage, drafting, templates, approval and sending | Automatic sending, scheduled sending or automatic follow-up emails |
| Follow-ups converted to tasks; task urgency and escalation signals | Reminder notifications |
| Weekly and monthly activity reports; Word download of the stored summary | PDF export, custom report templates |
| Manual calendar import for attendance tracking | Scheduled calendar import |
| Chat history kept in your browser | Server-side chat history or cross-device history |

---

## 2. Getting started and the interface

### Opening the application

1. Go to **[https://ai-shadow-mvp.vercel.app/](https://ai-shadow-mvp.vercel.app/)**.
   There is no login page.
2. In the left sidebar, find **Current Digital Twin** and choose a twin from
   the dropdown. Options are shown as `Name · Role`.
3. If the dropdown says *"None yet. Add one in Digital Twins."*, open
   **Digital Twins** and create one first (see [section 3](#3-digital-twins--setup-and-configuration)).
4. Open **Chat** and ask a question.

The note under the selector is accurate: *"Identity only, not sign-in. Sets
whose profile and memory shape answers."* Your choice is remembered in this
browser. When you switch twins, the whole workspace reloads with that twin's
data.

### Requirements and prerequisites

| Feature | Needs |
|---|---|
| Chat about documents | At least one indexed document in the Knowledge Base |
| Personalised answers | A twin with a profile and/or memories |
| Email inbox, triage, sending | An administrator-configured Outlook integration **and** a mailbox connected for your twin |
| Composing drafts and templates | Nothing extra; works without a mailbox |
| Activity reports | A connected mailbox (otherwise only the stored summary or task data is available) |
| OneDrive synchronisation | Administrator configuration on the server |

### The interface

The sidebar (the **☰ Menu** button on narrow screens) has a **Workspace**
section with six screens:

| Navigation item | Hint shown | Purpose |
|---|---|---|
| **Chat** | Ask your Digital Twin | Ask questions; see sources; attach documents |
| **Digital Twins** | Profiles, preferences, memory | Create, open, activate and delete twins; edit profile, preferences and memories |
| **Knowledge Base** | Documents and sync | Document counts and statuses, OneDrive status, manual upload, document list |
| **Email Agent** | Draft, triage, follow up | Inbox, Compose, Templates, Drafts, Follow-ups |
| **Tasks** | What you need to do | Your task list with due-date signals |
| **Reports** | Work report and email digests | Weekly/Monthly Activity Reports and *This week's work* |

On the **Chat** screen the sidebar also shows **New conversation** and your
conversation list, grouped as **Today**, **Yesterday** and **Older**. Hover
over a conversation to **Rename** or **Delete** it.

**Errors** appear in a red box. Where retrying makes sense there is a
**Try again** link. Messages come from the server and are shown as-is (for
example *"Could not reach the backend. Check that it is running."*).

---

## 3. Digital Twins — setup and configuration

### What a twin represents

A twin is one person: their identity (name, email, role), an optional profile,
and optional memories. Everything private in AI Shadow belongs to a twin,
including mailbox connection, triage results, drafts, templates, tasks,
calendar events and reports. Only the Knowledge Base is shared.

### Create a Digital Twin

1. Open **Digital Twins** and click **Add Digital Twin**.
2. In **Add a Digital Twin**, fill in all three fields. All are required, and
   **Create Digital Twin** stays disabled until they are filled:

   | Field | Notes |
   |---|---|
   | **Name** | Shown everywhere the twin appears |
   | **Email** | Must be unique. Creating a second twin with the same email fails |
   | **Role** | For example "Recruiter" or "CEO" |

3. Click **Create Digital Twin**. The new twin becomes the **active** twin
   automatically.

At this point the twin has an identity but no profile. Chat still works, and
answers are simply written without a persona.

### Configure the profile

1. On the twin's card, click **Open Twin**. The workspace has the tabs
   **Overview · Profile · Preferences · Memories**.
2. Open **Profile** and fill in the fields:

   | Field | Type | Required? |
   |---|---|---|
   | **Name** | Text | Required the **first** time the profile is saved |
   | **Role** | Text | Required the first time |
   | **Organization** | Text | Required the first time |
   | **Communication style** | One line ("Shapes how answers are written.") | Optional, up to 2,000 characters |
   | **Responsibilities** | List, one per line | Optional |
   | **Expertise** | List, one per line | Optional |
   | **Priorities** | List, one per line | Optional |
   | **Current focus** | List, one per line | Optional |

   Each list holds up to 25 items of up to 500 characters each.

3. Click **Save changes**. You should see *"Profile saved."*

**Editing later.** A blank text field on a later save means *keep the current
value*, so you never have to retype the whole profile. Changing **Name** or
**Role** here also updates how the twin is listed everywhere.

### Configure preferences

The **Preferences** tab edits two profile fields in one place:

- **Communication style**: "How answers should read. One line."
- **Decision preferences**: "One per line. Constrains what the twin recommends."

Click **Save preferences** (*"Preferences saved."*). Below the form,
**Preference memories** lists any memories of type *preference* (read-only
here; manage them on **Memories**).

### Add and manage memories

1. Open **Memories** and click **Add Memory**.
2. Fill in:
   - **Memory**: one durable thing worth remembering (1–2,000 characters).
   - **Type**: `fact`, `preference`, `decision`, `commitment` or `context`
     (default `fact`).
   - **Importance (1–5)**: default 3. Higher values are preferred when space
     is limited.
3. Click **Add memory**.

Each memory shows its type, a state badge (**Active**, **Expired** or
**Retired**), its importance and dates. Use **Edit** to change the text, or
**Delete** to remove it permanently.

> The UI has no "retire" button. A memory shows **Retired** only if it was
> deactivated through the API (`PATCH /memory/{id}` with `active: false`).
> Memories can carry an expiry date set through the API. Expired memories stop
> influencing answers.

### How a twin influences answers

- **Chat** (document questions): the profile and up to **8** active, unexpired
  memories (most important first) are placed above the retrieved passages,
  within a limit of about 2,000 characters. The model is told to use them for
  tone, emphasis and priorities, and **never** to cite them or treat them as
  evidence.
- **Chat** (greetings and "about you" questions): the reply is written from the
  profile and memories alone. If the twin has no profile, a fixed message asks
  you to add one rather than inventing a biography.
- **Email**: triage judges urgency against the twin's priorities. Drafts are
  written in the first person in the twin's voice.
- **Reports**: built from the twin's own mailbox and tasks.

Nothing is learned automatically. A twin only "knows" what has been entered on
its profile and memories.

### Selection, ownership and isolation

- The **Current Digital Twin** selector decides whose data every screen loads.
  **Make active** on a twin card does the same.
- The selected twin's id is sent with each request (the `X-User-ID` header).
  The server only returns that twin's private data.
- This is **identity, not authentication**. Anyone with access to the
  application can select any twin.

### Delete a twin

1. On the twin's card click **Delete**.
2. The dialog lists what **Will be permanently deleted** (tasks, stored
   reports, meetings, triaged messages, drafts, attachments, templates, mailbox
   configuration, memories, profile) and what **Will be kept** (shared
   Knowledge Base documents and every OneDrive source).
3. Type the twin's name to enable **Delete permanently**.

Deletion is **admin-only**. The request is made as the *currently selected*
twin, so that twin must be the administrator. On startup the server makes
exactly one user the administrator if none exists: the user whose email matches
`BOOTSTRAP_ADMIN_EMAIL`, otherwise the earliest-created user. There is no
screen for managing administrators.

---

## 4. Chat — user guide

### Start a conversation

1. Select a twin in **Current Digital Twin**.
2. Open **Chat**. Click **New conversation** to start a fresh thread.
3. Type in the box (*"Ask about anything in the knowledge base…"*) and press
   **Enter** or click **Send**. Use **Shift+Enter** for a new line.

The strip above the messages shows *Digital Twin: {name}*, *Knowledge base:
shared corpus* and *Sources in last answer: N*. While waiting you will see
*"Searching your documents…"*.

### What kind of message are you sending?

| Message type | Examples | What happens |
|---|---|---|
| **Greeting** | "Hi", "Good morning", "Thanks!" | A short conversational reply in the twin's voice. No documents are searched and no sources are shown |
| **Question about the twin** | "Introduce yourself", "Who are you?", "What are your priorities?" | Answered from the twin's profile and memories only. No sources. Without a profile, you are asked to add one |
| **Document question** | Anything else | The Knowledge Base is searched and the answer is grounded in the passages found |

A message that opens with a greeting but asks a real question ("Hi, what is our
leave policy?") is treated as a document question.

### How grounded answers work

For a document question, AI Shadow retrieves the most relevant passages (by
default the top 5), builds a context of up to about 12,000 characters, and asks
the model to:

- use only those passages, and not outside knowledge;
- say plainly when the passages do not answer the question;
- cite passages as `[SOURCE 1]`, `[SOURCE 2]` …;
- report disagreements between passages instead of merging them.

### Reading sources

Under each grounded answer, **SOURCES (N)** lists every passage that was given
to the model. Each card shows:

- the **document name**;
- the **match percentage** (cosine similarity between your question and the
  passage);
- a **location**: `Page N` for PDFs (slide number for PowerPoint) and the
  section heading where known.

Sources list what the model *saw*, not necessarily every passage it used. A
passage being listed does not prove that a particular sentence came from it.

### When nothing matches

If no indexed passage is relevant, the reply is:

> *"I could not find anything in your documents that answers that question."*

with the note *"Nothing in the knowledge base matched this question."* No model
call is made in this case. Try rephrasing, using terms that appear in the
documents, or check on **Knowledge Base** that the document you expect is
**indexed**.

### Conversation history and follow-ups

- Conversations are stored **only in your browser** (local storage). They are
  not on the server, do not follow you to another device or browser, and are
  lost if browser data is cleared.
- Conversations are titled from the first question. Use **Rename** and
  **Delete** in the sidebar.
- **Each question is answered on its own.** The server does not see earlier
  messages, so a follow-up such as "and what about the second one?" has no
  context. Repeat the subject in follow-ups: "What are the payment terms in the
  Freddie Mac subcontract?"

### Attaching documents in Chat

Click **Attach documents** to add files to the **shared Knowledge Base** from
the chat screen. Then click **Upload N file(s)**.

- Accepted: `.pdf, .docx, .pptx, .xlsx, .xlsm, .html, .htm, .txt, .md, .markdown`.
- Server limits: **10 MB** per file and **25** files per upload.
- Each file shows a result: **Indexed**, **Unchanged** (already present),
  **Replaced**, **Unsupported**, **Needs OCR** or **Failed**.

Attaching does **not** summarise the file in the conversation. It indexes it for
everyone. Once indexed, ask about it: *"Summarise the key points of
capability_statement.pdf"*.

### Example prompts

- *"What contract vehicles does SunRadia hold, and what are their numbers?"*
- *"Summarise our data-modernisation capabilities for a federal audience."*
- *"Which past performance examples involve master data management?"*
- *"What does the employee handbook say about holiday accrual?"*
- *"List the key technologies mentioned in the AI factory white paper."*
- *"Introduce yourself."* / *"What is your current focus?"*

### Interpreting answers and errors

| You see | Meaning | What to do |
|---|---|---|
| An answer with sources | Grounded in the listed passages | Open the source documents to verify important facts |
| An answer saying the context does not cover it | Passages were found but did not answer | Rephrase, or add the missing document |
| *"I could not find anything…"* | Nothing relevant was retrieved | Check the document is indexed; rephrase |
| *"The language model provider is unavailable."* | The model call failed (for example out of credit or a timeout) | Try again shortly; tell an administrator if it persists |
| *"The embedding provider is unavailable."* | Search could not run | As above |
| *"Could not reach the backend…"* | The server is down or unreachable | Try again; contact an administrator |

> AI answers can be incomplete or wrong even when sources are shown. Review
> anything you will act on or send externally.

---

## 5. Knowledge Base — documents and OneDrive

The Knowledge Base is the **shared** collection of company documents that Chat
and the Email Agent search. Every twin sees the same documents.

### The Knowledge Base screen

From top to bottom:

1. **Stat tiles**: Documents, Passages, Indexed, Processing, Unsupported,
   Failed. A note appears if some passages have no embedding yet (they cannot
   be retrieved).
2. **Knowledge Base source status**: the OneDrive panel (below).
3. **Manual upload**: the same upload tray as in Chat.
4. **Documents (N)**: search box (*"Search filenames…"*), filters **All /
   Indexed / Failed / Unsupported**, **Refresh**, and a table with columns
   **Document**, **Status**, **Source** (*Uploaded*, *OneDrive*, *External*),
   **Updated** and **Actions** (**Delete**). The list shows the 200 most recent
   documents.

### Upload documents

1. In **Manual upload**, click **Attach documents** and choose one or more
   files.
2. Click **Upload N file(s)**. Files are processed immediately, one request for
   the batch.
3. Read each file's result badge (hover for a description).

**Supported formats:** PDF, Word (`.docx`), PowerPoint (`.pptx`), Excel
(`.xlsx`, `.xlsm`; up to 2,000 rows per sheet), HTML (`.html`, `.htm`,
`.xhtml`), plain text (`.txt`) and Markdown (`.md`, `.markdown`).

**Refused with an explanation:** `.doc`, `.xls`, `.ppt` (save in the modern
format), `.msg`, `.zip`, `.vsd`, `.vsdx`, `.one`, `.mmap`. PDFs with no text
layer (scans) are marked **Needs OCR**. OCR is not available.

### What happens to a document

```mermaid
flowchart LR
    A["Upload or OneDrive file"] --> B{"Valid?<br/>size, type, not empty"}
    B -- no --> X["Rejected<br/>(unsupported / too large / empty)"]
    B -- yes --> C{"Same content<br/>already indexed?"}
    C -- yes --> U["Unchanged<br/>(nothing re-processed)"]
    C -- no --> D["Parse text<br/>pages, headings, slides, sheets"]
    D -- no text --> O["ocr_required"]
    D -- parse error --> F["failed"]
    D --> E["Split into ~1,000-char passages<br/>(150-char overlap)"]
    E --> G["Create embeddings<br/>(1,536-dimension vectors)"]
    G --> H["Store in PostgreSQL + pgvector<br/>status: indexed"]
```

- **Parsing** keeps where each passage came from (page, slide, sheet,
  heading), which is what the source cards show.
- **Chunking** splits each section into passages of about 1,000 characters with
  a 150-character overlap. A passage never spans two sections.
- **Embeddings** turn each passage into a vector. **Indexing** stores the
  vectors in PostgreSQL with an HNSW cosine index (pgvector), which makes
  semantic search fast.
- **Duplicates.** A file is identified by its content (a SHA-256 hash), not
  its name. Uploading identical bytes again returns **Unchanged** and costs
  nothing.

### Document statuses

| Status | Meaning | Searchable? |
|---|---|---|
| `pending` / `processing` | Being ingested | No |
| `indexed` | Parsed, chunked and embedded | **Yes** |
| `unsupported` | Format not supported, or a synced file over the size limit | No |
| `ocr_required` | No text layer (scanned document) | No |
| `failed` | Parsing or embedding failed; the error is shown under the filename | No |

### Updating and re-indexing

There is **no Reindex button**. What happens depends on where the document came
from:

- **OneDrive documents** are re-indexed automatically when the file changes in
  OneDrive and a sync runs. The old passages are replaced **only after** the new
  version parses successfully. If the new version fails, the document is marked
  failed and is not searchable until fixed.
- **Manually uploaded documents**: an edited file is a *new* document. The old
  version stays in the Knowledge Base, so **delete the old version** to avoid
  conflicting answers. Re-uploading identical bytes of a *failed* document
  retries it in place.
- **Missing embeddings**: if embedding failed (for example a provider outage),
  an administrator can run `python -m scripts.backfill_embeddings` (see
  [section 11](#11-installation-and-local-development)).

**Delete** removes a document and all its passages immediately.

### Troubleshooting ingestion

| Result / error | Cause | Fix |
|---|---|---|
| **Unsupported** / HTTP 415 | Unsupported or legacy format | Save as PDF/DOCX/PPTX/XLSX |
| HTTP 413 | File over 10 MB, or more than 25 files | Split the file or the batch |
| **Needs OCR** | Scanned PDF | Use a text-based PDF |
| **Failed**, with a parse error | Corrupt or password-protected file | Re-save the file and upload again |
| **Failed**, embedding provider error (502) | Model provider outage or out of credit | Retry later; administrator can backfill embeddings |
| "N passages have no embedding" | Embeddings missing | Administrator runs the backfill script |

### OneDrive / SharePoint synchronisation *(optional, administrator-configured)*

OneDrive is the managed source for the corpus. Folders are configured **on the
server**, not in the UI.

The **Knowledge Base source status** panel shows one of:

- **Not configured**: no OneDrive credentials are set on the server.
- **Configuration error**: the `ONEDRIVE_SOURCES` setting is malformed; the
  message says what is wrong.
- **Configured**: shows whether automatic sync is on (*"Synchronising
  automatically every N minutes."* or *"Automatic synchronisation is off; runs
  are manual."*), the last full sync, and a table of sources.

Each source row shows **Status** (*Not yet synced, Syncing, Synced, Partial,
Failed*), **Last synchronised**, **Files** and **Details**. **Show** reveals
counts for *Examined, Added, Updated, Deleted, Unsupported, Needs OCR, Failed*
and a **Sync this source** button. **Sync now** syncs every source; **Refresh**
reloads the status.

**Discovery is not indexing.** A sync *examines* every file in the folder, but
only supported files within the size limit are downloaded and indexed:

- Unsupported or oversized files are recorded as **unsupported** without being
  downloaded.
- A download failure is recorded as **failed**; any previously indexed version
  keeps its passages.
- A file deleted in OneDrive is removed from the Knowledge Base when Graph
  reports the deletion.
- **Partial** means the run finished but some files failed. Check the
  per-source counts and the document list (filter **Failed**).

After the first full run, syncs are **incremental**: only changed files are
processed. Setup instructions are in
[section 11.11](#1111-optional-microsoft-graph-onedrive-mail-calendar).

---

## 6. Email Agent — full workflow

Open **Email Agent**. The header reads *"Drafts written as your Digital Twin,
from the shared company knowledge base. Every send is approved by you."* Tabs:
**Inbox · Compose · Templates · Drafts · Follow-ups**.

> **Nothing is ever sent automatically.** Generating a draft only produces
> text. An email leaves only after you click **Approve for sending** and then
> **Send**, and any edit after approval withdraws it.

### 6.1 Mailbox setup

**Prerequisites (administrator):** the server must have
`EMAIL_PROVIDER=outlook` and Microsoft Graph credentials. The Entra application
needs the application permissions **Mail.Read** and **Mail.Send** with admin
consent ([section 11.11](#1111-optional-microsoft-graph-onedrive-mail-calendar)).

**Connecting a twin's mailbox (user).** The connection form is on the
**Reports** screen, above the Weekly/Monthly Activity Report:

1. Select the twin, open **Reports**, and find **Mailbox for {name}**.
2. Enter **Mailbox address** (for example `person@sunradia.com`).
3. Click **Connect** (or **Change**). You should see *"Mailbox set to …"*.
4. **Disconnect** removes only the link. No mail is touched.

**Checking the connection.** The **Mailbox** banner at the top of Email Agent
shows **Connected**, **Not connected** or **Connection failed**. *Connection
failed* usually means the Graph permissions are missing or the address is not a
mailbox in the tenant. The status check names the missing permission.

**Shared fallback.** If the server sets `EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX=true`,
a twin without its own mailbox uses `EMAIL_MAILBOX_ADDRESS`, and the mailbox
form shows **Shared fallback** with a warning. This is off by default.

Without a mailbox you can still use **Compose**, **Templates** and **Drafts**.
You cannot read the inbox or send.

### 6.2 Inbox

- **Inbox (N)** lists up to **50** of the most recent messages. **Load more**
  adds 50 at a time, up to **200**. **Refresh** reloads.
- Messages are grouped by triage category: **Urgent → Needs reply → Follow-up
  → FYI → Low priority → Not yet triaged**.
- Each row shows the subject, sender, date, attachment count and a preview.
  There is no separate full-message view. Open the email in Outlook to read it
  in full. Received attachments cannot be downloaded from AI Shadow.

### 6.3 Triage

Triage is an AI assessment of **one message, on request**. Nothing is triaged
in the background.

1. Click **Triage** on a message (it changes to **Re-assess** afterwards).
2. The row now shows:
   - **Category**: Urgent, Needs reply, Follow-up, FYI or Low priority;
   - **Priority**: **High / Normal / Low** (separate from category);
   - a one-paragraph **summary**;
   - **Suggested:** the recommended next step;
   - **action items**;
   - whether a **follow-up** is recommended, why, and a due date **only if the
     email itself states or implies one**.

Triage takes your twin's priorities into account. It does **not** search the
Knowledge Base. The assessment (not the email body) is stored, and re-triaging
updates it. Each triage is one model call.

Use the result to decide: reply now (**Draft a reply**), add the follow-up to
Tasks (**Follow-ups** tab), or leave FYI and low-priority mail.

### 6.4 Drafting a reply

1. On an inbox message, click **Draft a reply**.
2. AI Shadow fetches the full message, writes a reply in your twin's voice
   using the **original subject, body and sender**, your **profile and
   memories**, and relevant **Knowledge Base** passages. It then saves the
   result as a draft with status **Needs review** and opens it in **Compose**.
3. Review the **To**, **Subject** and **Message**, and edit as needed.
4. Use **Save draft** to keep it, then follow
   [Review, approve and send](#66-review-approve-and-send).

The reply is linked to the original message, so it is sent as a threaded
reply. Very long messages are passed in as received. If generation fails you
see *"The reply could not be drafted."* with the server's reason. Try again, or
write the reply in **Compose**.

### 6.5 Compose and revise

1. Open **Compose** (*"Writing as {name} · {role}"*).
2. In **What should this email do?**, describe the email, for example *"Write a
   concise follow-up after yesterday's meeting and ask for a decision by
   Friday."*
3. Optionally tick **Use the company knowledge base** (off by default) or pick
   **Start from a template…**.
4. Click **Generate draft**. A note says whether the twin's profile was used
   and whether company knowledge was retrieved, listing the passages if so.
   Without retrieved knowledge the draft makes no claims about SunRadia.
   Missing facts are left as placeholders such as `[date]`.
5. Refine with **Rewrite**, **Improve clarity**, **Shorten**, **Expand**,
   **More professional**, **More concise**, **Change tone** (enter a tone, for
   example "warmer"), or **Suggest subject**.
6. Fill in **To**, optionally **Cc/Bcc** (**Add Cc / Bcc**), and **Subject**.
   Separate several addresses with commas, semicolons or new lines. Invalid
   ones are flagged as *"Not an email address: …"*.
7. **Attachments:** save the draft first, then **Attach a file** (any type, up
   to **10 MB** each and **10** per draft).

### 6.6 Review, approve and send

| Step | Button | Result |
|---|---|---|
| Save | **Save draft** / **Save changes** | *"Draft saved."* Status **Draft** (or **Needs review** for AI-generated drafts) |
| Approve | **Approve for sending** | Requires at least one recipient, a subject and a message. *"Approved. It will be sent only when you press Send."* Status **Ready to send** |
| Send | **Send** | *"Sent — the mailbox confirmed it."* Status **Sent** |

- **Editing an approved draft** (text, recipients or attachments) withdraws the
  approval, so you must approve again.
- Under the buttons, AI Shadow lists what still blocks sending (for example
  *"Review the draft and approve it."* or *"Connect a mailbox on the server
  before sending."*).
- If the mailbox refuses the email, the draft becomes **Failed** with the
  reason (*"The mailbox refused this: …"*). The approval is cleared so you can
  fix and retry.
- Emails are sent as **plain text**. Replies are threaded to the original.
  Sending is irreversible.

### 6.7 Drafts

**Drafts** lists all saved drafts with filters **All / Needs review / Ready to
send / Sent / Failed**. Use **Review**, **Continue editing** or **View**, and
**Delete**. Deleting a sent draft removes only AI Shadow's record and does not
unsend anything.

### 6.8 Templates

Templates are reusable, **private** emails with placeholders written as
`{{ name }}`.

1. **Templates → New template**. Fill in **Name** (required, unique per twin),
   **Category** (*Introduction, Follow-up, Meeting request, Proposal follow-up,
   Thank you, Outreach, Custom*), **Description**, **Subject** and **Body**.
   Click **Save template**.
2. To use one, click **Use**, fill in each placeholder and click **Open as a
   draft**. Filling a template is plain text substitution with no AI. Then
   revise it with the Compose buttons if you wish.

### 6.9 Follow-ups

When triage recommends a follow-up, it appears in **Follow-ups** under
**Overdue** or **Outstanding** (tick **Show handled** to include finished
ones). Each row offers:

- **Add to Tasks**: creates one task (pressing it again never duplicates it).
  Then **View task**.
- **Draft a follow-up**: generates a follow-up draft (with Knowledge Base use)
  and opens it in Compose.
- **Mark handled** / **Reopen**.

Follow-ups are recommendations only. There are no reminders, and nothing is
scheduled or sent.

### 6.10 Calendar *(partial)*

The **Reports → This week's work** view has **Import from Outlook**, which
reads meetings for a date window, and lets you record attendance (**Attended /
Missed / Not sure**). It needs the **Calendars.Read** permission, which has
**not** been granted in the current deployment, so import is expected to fail
there. Import is manual only, and attendance is never inferred from an
invitation.

---

## 7. Tasks

**Tasks** lists everything you still have to do.

1. Click **+ New task** and fill in **What needs doing** (required, up to 500
   characters), optional **Summary**, optional **Due** date and **Priority**
   (*Low, Normal, High, Urgent*). Click **Add task**.
2. Tasks are grouped by signal:

| Group | Meaning |
|---|---|
| **Urgent** | More than two days overdue |
| **Attention** | Two days overdue |
| **To do** | Not yet due, or only just past (or no due date) |
| **Done** | Finished |

3. Click **Mark done** when finished. Tasks created from email show *From email
   · {contact}*.

Overdue, urgency and escalation are calculated when you look, never stored.

---

## 8. Reports

Open **Reports** (*"A short management summary of what happened, for a week or
a month."*). There are three tabs.

### 8.1 Weekly and Monthly Activity Report

These describe the selected twin's **mailbox activity** for a period, using
correspondence analysis and, where configured, the language model.

**Prerequisite:** a connected mailbox (the **Mailbox for {name}** form is at
the top of this tab).

1. Choose **Weekly Activity Report** or **Monthly Activity Report**.
2. Choose a **Period** from the dropdown. The current, still-running period is
   marked **(in progress)**. Weeks start on Monday, and periods are in UTC.
3. The report loads. A stored report for a closed period appears almost
   instantly. A new one is generated, which can take a minute or more.
4. **Regenerate** rebuilds it from the mailbox, ignoring the stored version.
5. **Download** saves the stored **summary report** for that period as a Word
   (`.docx`) file. This is the only export format, and the download is the
   stored summary rather than the detailed on-screen analysis.

**What happens during generation**

1. The mailbox is read for the window (Inbox and Sent Items).
2. Messages are grouped into conversations and split into *substantive* vs
   *routine/automated* threads by deterministic rules.
3. If a language model is configured, substantive threads are analysed in small
   batches (at most 120 threads, 90 seconds of model time in total), and a
   narrative is composed. Longer windows are analysed week by week, then
   combined.
4. If the model is unavailable, too slow or out of budget, the report falls
   back to **Structural analysis only**: counts and thread lists without AI
   narrative.
5. The result is stored. A closed period is never regenerated unless you press
   **Regenerate**. A running period is re-checked after 30 minutes and rebuilt
   only if new mail arrived.

**Reading the report**

- Badges show the period, **Partial period** / **Provisional** (still
  running), whether it was **Freshly generated** or **Cached**, and **Narrative
  analysis** vs **Structural analysis only**.
- Figures: *Messages analysed, Substantive threads, Routine / automated,
  Workstreams, Conversations described*.
- **Known limitations** and **Quality notes** explain gaps (for example a
  missing calendar or too little detail).

**Long-running and concurrent requests.** If someone else is already
generating the same report, you see *"This report is being generated"*. The
page checks again every 5 seconds for about 4 minutes, then offers **Check
again**.

**When it fails**

- **No mailbox connected**: the stored summary is shown instead, with an amber
  note.
- **Other failures**: *"The server failed to generate this report (HTTP N)."*
  Try **Regenerate** later. Model credit or rate limits can cause failures or a
  structural-only report.
- **Recent reports** lists up to five stored reports, each with **Open**.

### 8.2 This week's work

A live, deterministic view (no AI) of the twin's work:

- tiles for **Overdue**, **Due soon**, **Escalations**, **Completed** and
  **Meetings to confirm**;
- **Escalation required** cards (reason, suggested action, target) with **Mark
  resolved**. **Prepare escalation email** needs `ESCALATION_CONTACT_ADDRESS`
  on the server and does not currently open a draft;
- tasks with **Mark complete** and **Start**;
- **Meetings** with **Import from Outlook** and attendance buttons;
- email follow-ups.

> Reports summarise email automatically and can omit or misjudge context. Treat
> them as a starting point and verify before circulating.

---

## 9. How the platform works

### Architecture

```mermaid
flowchart TB
    user["Browser<br/>React + Vite SPA"]

    subgraph vercel["Vercel"]
        spa["Static frontend<br/>(dist/)"]
        proxy["/api/* rewrite<br/>adds x-origin-secret"]
    end

    subgraph render["Render web service: FastAPI (uvicorn)"]
        mw["Origin-secret check"]
        routes["API routes<br/>chat, profile, memory, users,<br/>documents, search, sync,<br/>email, tasks, events, reports"]
        chat["Chat + intent + persona"]
        ingest["Ingestion<br/>parse, chunk, embed"]
        retr["Retrieval<br/>pgvector cosine search"]
        email["Email Agent<br/>triage, compose, drafts, send"]
        reports["Reports<br/>activity, digests, weekly work"]
        sync["OneDrive sync<br/>+ optional schedulers"]
        llm["LLM layer<br/>OpenAI-compatible client"]
        graphc["Microsoft Graph client"]
    end

    db[("PostgreSQL + pgvector<br/>SQLAlchemy / Alembic")]
    provider["OpenRouter<br/>(or OpenAI)<br/>chat + embeddings"]
    ms["Microsoft Graph<br/>OneDrive / Outlook mail / calendar"]

    user --> spa
    user --> proxy --> mw --> routes
    routes --> chat & ingest & email & reports & sync
    chat --> retr --> db
    chat --> llm
    ingest --> llm
    ingest --> db
    email --> retr
    email --> llm
    email --> graphc
    reports --> graphc
    reports --> llm
    reports --> db
    sync --> graphc
    sync --> ingest
    llm --> provider
    graphc --> ms
    routes --> db
```

### Main data flows

**1. Chat with Knowledge Base retrieval**

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI /chat
    participant I as Intent classifier
    participant R as Retrieval
    participant DB as Postgres/pgvector
    participant M as LLM provider
    U->>API: question + X-User-ID
    API->>I: classify (no model call)
    alt greeting or question about the twin
        API->>DB: load twin profile + memories
        API->>M: twin_conversation prompt
        M-->>U: reply (no sources)
    else document question
        API->>M: embed question
        API->>R: top-k similar passages
        R->>DB: cosine search (indexed docs only)
        alt nothing found
            API-->>U: fixed no-match answer (no model call)
        else passages found
            API->>DB: load profile + memories
            API->>M: rag_answer prompt (persona + passages)
            M-->>U: answer + sources
        end
    end
```

**2. Document ingestion and indexing**

```mermaid
flowchart LR
    up["Upload<br/>POST /documents/upload<br/>or batch-upload"] --> v["Validate"]
    od["OneDrive sync<br/>POST /sync/onedrive"] --> dl["Delta query +<br/>download"] --> v
    v --> h["Hash: dedupe"] --> p["Parse"] --> c["Chunk"] --> e["Embed<br/>(provider)"] --> s[("document_chunks<br/>vector + HNSW")]
```

**3. Email triage and reply drafting**

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI
    participant G as Microsoft Graph
    participant M as LLM provider
    participant DB as Postgres
    U->>API: GET /email/messages
    API->>G: list inbox (user's mailbox)
    U->>API: POST /email/triage {message_id}
    API->>G: fetch message
    API->>M: email_triage prompt (+ twin persona)
    API->>DB: store assessment
    U->>API: POST /email/compose (reply)
    API->>DB: retrieve KB passages (optional)
    API->>M: email_compose prompt
    U->>API: POST /email/drafts, then approve
    U->>API: POST /email/drafts/{id}/send {confirm:true}
    API->>G: reply / sendMail
```

**4. Report generation**

```mermaid
flowchart LR
    req["GET /reports/activity<br/>(start, end)"] --> st{"Stored and fresh?"}
    st -- yes --> out["Return stored report"]
    st -- no --> lock{"Already generating?"}
    lock -- yes --> p202["202: in progress"]
    lock -- no --> rd["Read mailbox via Graph"] --> cls["Group + classify threads"]
    cls --> llmq{"LLM configured<br/>and within budget?"}
    llmq -- yes --> nar["Batch analysis + synthesis"]
    llmq -- no --> str["Structural analysis"]
    nar --> save[("activity_report")]
    str --> save --> out
```

### Design principles in the code

- **Provider abstraction.** Only `backend/app/services/llm/client.py` knows
  provider names. Switching between OpenRouter and OpenAI is configuration.
- **Prompts are registered templates** in `backend/app/prompts/system.py`.
- **Sources come from retrieval, not from the model**, so the model cannot
  invent a document name.
- **Domain errors, not HTTP errors.** Services raise exceptions from
  `app/core/exceptions.py`, and `app/main.py` maps them to status codes.
- **Derived facts are computed on read** (overdue, urgency, escalation).

---

## 10. Technology stack

| Technology | Purpose | Where |
|---|---|---|
| React 18 + TypeScript | User interface | `frontend/src` |
| Vite 5 | Dev server (with `/api` proxy) and build | `frontend/vite.config.ts` |
| Tailwind CSS 3 (PostCSS, Autoprefixer) | Styling | `frontend/tailwind.config.js` |
| ESLint, Prettier | Frontend lint and format | `frontend/.eslintrc.json`, `.prettierrc.json` |
| Node test runner + `tsx` + jsdom | Frontend tests | `frontend/src/tests` |
| Python 3.11 | Backend runtime | `backend/` |
| FastAPI + Uvicorn | HTTP API | `backend/app/main.py`, `app/api/` |
| Pydantic / pydantic-settings | Schemas and configuration | `app/schemas/`, `app/config/settings.py` |
| SQLAlchemy 2 | ORM / database access | `app/models/`, `app/database/` |
| Alembic | Database migrations (0001–0016) | `backend/alembic/` |
| PostgreSQL 16 + pgvector | Data and vector search (HNSW, cosine) | `docker-compose.yml`, migration 0001 |
| psycopg 3 / psycopg2 | PostgreSQL drivers | `requirements.txt` |
| OpenAI Python SDK | Client for OpenRouter/OpenAI (Chat Completions, embeddings) | `app/services/llm/` |
| OpenRouter | Default LLM provider (`openai/gpt-oss-20b`) and embeddings (`openai/text-embedding-3-small`) | `LLM_*`, `EMBEDDING_*` settings |
| LangChain text splitters | Passage chunking | `services/features/documents/chunker_service.py` |
| pypdf, python-docx, python-pptx, openpyxl | Document parsing | `parser_service.py` |
| Microsoft Graph (client credentials) | OneDrive/SharePoint, Outlook mail, calendar | `app/services/graph/`, `app/services/email/provider/` |
| python-docx | Report `.docx` export | `services/features/reports/document_service.py` |
| pytest, httpx | Backend tests | `backend/tests` |
| Ruff | Python lint and format (installed separately) | `backend/pyproject.toml` |
| Vercel | Frontend hosting and API proxy | `frontend/vercel.json` |
| Render | Backend hosting | `render.yaml` |
| Neon | Managed PostgreSQL used in production (via `DATABASE_URL`) | Deployment configuration |
| Docker Compose | Local PostgreSQL + pgvector | `docker-compose.yml` |

---

## 11. Installation and local development

Commands are given for **Windows PowerShell** (the main development
environment). macOS/Linux equivalents are noted where they differ.

### 11.1 Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | **3.11** | Production pins 3.11.11 (`render.yaml`); Ruff targets `py311` |
| Node.js + npm | Current LTS (20 or 22 recommended) | No `engines` field is declared; the test runner needs `node --import` (Node ≥ 20.6) |
| Docker Desktop | Any recent | For local PostgreSQL + pgvector (or use your own PostgreSQL 16 with pgvector) |
| Git | Any | |
| API key | OpenRouter (or OpenAI) | Needed for chat, embeddings, triage and drafting |

### 11.2 Clone the repository

```powershell
git clone <repository-url> ai-shadow-mvp
cd ai-shadow-mvp
```

### 11.3 Start PostgreSQL with pgvector

```powershell
docker compose up -d
```

This starts `pgvector/pgvector:pg16` as container `aishadow-postgres` on port
**5432**, with database, user and password all `aishadow`. The `vector`
extension is created by the first migration, so no manual step is needed.

### 11.4 Backend: virtual environment and dependencies

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install ruff                       # linter/formatter, not in requirements.txt
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### 11.5 Backend configuration

The template lives at the **repository root** and is copied into `backend/`:

```powershell
Copy-Item ..\.env.example .env         # macOS/Linux: cp ../.env.example .env
```

Settings are read from `.env` in the **current working directory**, so always
run the backend, Alembic and scripts from `backend/`. Every setting has a
default in `backend/app/config/settings.py`. Never commit `.env`.

**Key variables** (placeholders only):

| Category | Variables | Notes |
|---|---|---|
| Database | `DATABASE_URL` | Local default `postgresql+psycopg2://aishadow:aishadow@localhost:5432/aishadow`. `postgresql://` and `postgresql+psycopg://` (psycopg 3) also work |
| LLM | `LLM_PROVIDER` (`openrouter` or `openai`), `LLM_MODEL` (default `openai/gpt-oss-20b`), `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | The key for the selected provider is required for chat |
| LLM bounds | `LLM_TIMEOUT_SECONDS` (45), `LLM_MAX_RETRIES` (1) | Output is capped at 4,096 tokens per call in code |
| Embeddings | `EMBEDDING_PROVIDER` (empty means use `LLM_PROVIDER`), `EMBEDDING_MODEL` (`openai/text-embedding-3-small`), `EMBEDDING_DIMENSIONS` (1536), `EMBEDDING_BATCH_SIZE` | Changing dimensions needs a migration |
| Ingestion | `MAX_UPLOAD_SIZE_BYTES` (10 MB), `MAX_BATCH_UPLOAD_FILES` (25), `CHUNK_SIZE` (1000), `CHUNK_OVERLAP` (150) | |
| Retrieval / chat | `RETRIEVAL_TOP_K` (5), `RETRIEVAL_SIMILARITY_THRESHOLD` (0.0), `CHAT_CONTEXT_MAX_CHARS` (12000), `PERSONA_CONTEXT_MAX_CHARS` (2000), `MAX_MEMORIES_IN_CONTEXT` (8) | |
| Reports | `REPORT_ANALYSIS_BUDGET_SECONDS` (90), `ACTIVITY_REPORT_FRESH_SECONDS` (1800), `ACTIVITY_REPORT_LOCK_SECONDS` (600), `REPORT_DIGEST_SCHEDULE_ENABLED`, `REPORT_DIGEST_INTERVAL_SECONDS` | |
| OneDrive / Graph | `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET`, `ONEDRIVE_DRIVE_ID`, `ONEDRIVE_SOURCES`, `ONEDRIVE_SYNC_ENABLED`, `ONEDRIVE_SYNC_INTERVAL_SECONDS`, `ONEDRIVE_MAX_FILE_BYTES`, … | Optional. The credentials are shared with mail and calendar |
| Email Agent | `EMAIL_PROVIDER` (`outlook` or empty), `EMAIL_MAILBOX_ADDRESS`, `EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX`, `EMAIL_MAX_ATTACHMENT_BYTES`, `EMAIL_MAX_ATTACHMENTS_PER_DRAFT`, `EMAIL_RETRIEVAL_TOP_K`, `EMAIL_CONTEXT_MAX_CHARS`, `EMAIL_INBOX_PAGE_SIZE`, `EMAIL_INBOX_MAX_PAGE_SIZE` | Optional |
| Tasks | `TASK_WARNING_DAYS` (3), `TASK_ESCALATION_DAYS` (7), `ESCALATION_CONTACT_ADDRESS`, `ESCALATION_CONTACT_NAME` | |
| Admin / app | `BOOTSTRAP_ADMIN_EMAIL`, `APP_NAME`, `DEBUG`, `LOG_LEVEL` | |
| Perimeter | `ORIGIN_SECRET` | **Leave unset locally.** Used only behind the Vercel proxy |

Minimal local example:

```dotenv
DATABASE_URL=postgresql+psycopg2://aishadow:aishadow@localhost:5432/aishadow
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-oss-20b
OPENROUTER_API_KEY=<your-openrouter-key>
```

### 11.6 Database migrations

```powershell
alembic upgrade head          # from backend/, with the venv active
alembic current               # should report 0016 (head)
```

Alembic reads the URL from `DATABASE_URL` (`alembic.ini` deliberately has
none).

### 11.7 Verify the model provider (optional but recommended)

```powershell
python -m scripts.verify_embedding_provider
```

This makes one live embedding call and prints the vector width, which must
match `EMBEDDING_DIMENSIONS`.

### 11.8 Start the backend

```powershell
uvicorn app.main:app --reload
```

The API runs at `http://localhost:8000`, with interactive docs at
`http://localhost:8000/docs`.

### 11.9 Frontend

In a second terminal:

```powershell
cd frontend
npm install                   # or: npm ci
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api/*` to the
backend and strips the `/api` prefix. Optional settings
(`frontend/.env.example`, copy to `frontend/.env.local` if needed):

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | Where the browser sends API calls. Leave unset to use the proxy |
| `VITE_BACKEND_ORIGIN` | `http://localhost:8000` | Where the dev proxy forwards `/api` |

The backend has **no CORS middleware**. Always go through the proxy rather than
pointing `VITE_API_BASE_URL` at another origin.

### 11.10 Verify the installation

```powershell
Invoke-RestMethod http://localhost:8000/health   # -> status: healthy
```

Then in the browser:

1. **Digital Twins → Add Digital Twin** and create a twin.
2. **Knowledge Base → Manual upload**: upload a small PDF or DOCX and confirm
   it shows **Indexed**.
3. **Chat**: ask a question about it and confirm sources appear.

### 11.11 Optional: Microsoft Graph (OneDrive, mail, calendar)

A deployment without Graph credentials works normally. OneDrive shows *Not
configured*, and the inbox and sending are unavailable.

1. **Register an application** in Microsoft Entra ID. Note the *Application
   (client) ID* and *Directory (tenant) ID*, and create a **client secret**
   (shown once; never commit it).
2. **Grant Microsoft Graph Application permissions** (not Delegated) and click
   **Grant admin consent**:

   | Permission | Needed for |
   |---|---|
   | `Files.Read.All` | OneDrive / SharePoint synchronisation |
   | `Mail.Read`, `Mail.Send` | Email Agent inbox, triage, sending |
   | `Calendars.Read` | Calendar import |

   Each is a separate consent. Files permission does not grant mail access.
3. Set `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID` and `ONEDRIVE_CLIENT_SECRET`.
   For email, also set `EMAIL_PROVIDER=outlook`.
4. **Find drives and folders:**

   ```powershell
   python -m scripts.discover_onedrive_sources --user someone@example.com
   python -m scripts.discover_onedrive_sources --drive <drive-id>
   python -m scripts.discover_onedrive_sources --url "<sharepoint-folder-url>"
   python -m scripts.verify_graph_access
   ```

5. **Configure sources.** `ONEDRIVE_SOURCES` is a one-line JSON array:

   ```dotenv
   ONEDRIVE_SOURCES=[{"key":"capabilities","label":"Capabilities","path":"Documents/Capabilities"}]
   ```

   | Field | Required | Notes |
   |---|---|---|
   | `key` | yes | Stable id. Renaming it forces a full resync |
   | `label` | no | Display name |
   | `drive_id` | no | Defaults to `ONEDRIVE_DRIVE_ID` |
   | `user` | one of | A user principal name; uses that user's OneDrive |
   | `path` | one of | Folder path from the drive root |
   | `item_id` | one of | Folder id; survives renames |
   | `share_url` | one of | A sharing link inside the tenant (consumer OneDrive links cannot be read) |
   | `uri` | no | Link shown in status screens only |
   | `enabled` | no | `false` pauses the source |

   Pin paths to ids once known: `python -m scripts.discover_onedrive_sources --resolve`.
6. **Run and check:** use **Sync now** in the UI, or:

   ```powershell
   python -m scripts.diagnose_onedrive_corpus           # read-only inventory
   python -m scripts.validate_live_integration --full   # config → auth → sync → calendar → retrieval
   ```

7. Optional scheduling: `ONEDRIVE_SYNC_ENABLED=true` and
   `ONEDRIVE_SYNC_INTERVAL_SECONDS`. Each server process runs its own timer.

### 11.12 Tests and checks

**Backend** (from `backend/`). Tests use in-memory SQLite, mock every model
call, block outbound network access, and never read `.env`:

```powershell
pytest tests/services/features/chat -q   # a focused area
pytest                                   # full suite
pytest -m postgres                       # live-Postgres tests (need docker compose up)
ruff check app tests alembic
ruff format --check app tests alembic
```

**Frontend** (from `frontend/`):

```powershell
npm test
npm run lint
npm run typecheck
npm run build
```

### 11.13 Common installation and runtime errors

| Symptom | Likely cause | Fix |
|---|---|---|
| `connection refused` on port 5432 | PostgreSQL not running | `docker compose up -d`; check `docker ps` |
| `type "vector" does not exist` | Database without pgvector | Use the `pgvector/pgvector` image, or install pgvector on your server |
| `ModuleNotFoundError: No module named 'psycopg'` | Old dependency install | `pip install -r requirements.txt` again |
| Alembic connects to the wrong database | Run outside `backend/`, so `.env` was not found | `cd backend` first |
| `Activate.ps1 cannot be loaded` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Every chat answer returns 502 | Missing or invalid API key, or no provider credit | Set the key for `LLM_PROVIDER`; check your OpenRouter balance |
| `does not match the configured vector width` | `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` disagree | Use a 1536-dimension model or migrate |
| Frontend shows *"Could not reach the backend"* | Backend not running, or the proxy targets the wrong origin | Start uvicorn; check `VITE_BACKEND_ORIGIN` |
| 403 `OriginNotAllowed` locally | `ORIGIN_SECRET` set in local `.env` | Remove it for local development |

---

## 12. Deployment guide

**Production:** [https://ai-shadow-mvp.vercel.app/](https://ai-shadow-mvp.vercel.app/)

```mermaid
flowchart LR
    b["Browser"] --> v["Vercel<br/>static SPA"]
    v -- "/api/* → https://ai-shadow-api.onrender.com/*<br/>+ x-origin-secret" --> r["Render<br/>ai-shadow-api (FastAPI)"]
    r --> n[("Neon PostgreSQL<br/>+ pgvector")]
    r --> o["OpenRouter"]
    r --> g["Microsoft Graph"]
```

### Frontend: Vercel

Configured by `frontend/vercel.json`:

- Vite framework, `npm run build`, output `dist`.
- **API routing:** requests to `/api/(.*)` are proxied to
  `https://ai-shadow-api.onrender.com/$1` (the `/api` prefix is stripped), and
  Vercel adds the header `x-origin-secret` from its `ORIGIN_SECRET`
  environment variable.
- Every other path falls back to `index.html` (single-page app).
- `VITE_API_BASE_URL` can stay unset; the default `/api` matches the rewrite.
- The backend hostname is **hard-coded** in `vercel.json`. A renamed Render
  service requires editing that file.

### Backend: Render

Configured by `render.yaml` (Blueprint):

| Setting | Value |
|---|---|
| Service | `ai-shadow-api`, Python runtime, `rootDir: backend`, 1 instance |
| Build | `pip install -r requirements.txt` |
| Pre-deploy | `alembic upgrade head` (migrations run before each deploy) |
| Start | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check | `GET /health` → `{"status": "healthy"}` |
| Fixed env | `PYTHON_VERSION=3.11.11`, `DEBUG=false`, `LOG_LEVEL=INFO`, `LLM_PROVIDER=openrouter`, `LLM_MODEL=openai/gpt-oss-20b:nitro`, `EMAIL_PROVIDER=outlook`, `EMAIL_ALLOW_SHARED_FALLBACK_MAILBOX=false`, `ONEDRIVE_SYNC_ENABLED=false`, `REPORT_DIGEST_SCHEDULE_ENABLED=false` |
| Prompted secrets | `ORIGIN_SECRET`, `OPENROUTER_API_KEY`, `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET`, `BOOTSTRAP_ADMIN_EMAIL` |

### Database: Neon PostgreSQL

Production uses **Neon** PostgreSQL, supplied to the backend through
`DATABASE_URL`. Note that the Blueprint in `render.yaml` also declares a
Render-managed database (`ai-shadow-db`) and binds `DATABASE_URL` to it, so
when deploying with Neon, `DATABASE_URL` on the Render service must point to
the Neon connection string. A Neon URL of the form
`postgresql://…?sslmode=require` uses the psycopg 3 driver, which is installed.
The first migration runs `CREATE EXTENSION IF NOT EXISTS vector`, which Neon
supports. Connections use `pool_pre_ping` to survive idle disconnects.

### Environment variable categories

| Where | Category | Variables |
|---|---|---|
| Render | Database | `DATABASE_URL` |
| Render | Model provider | `LLM_PROVIDER`, `LLM_MODEL`, `OPENROUTER_API_KEY` (plus optional `EMBEDDING_*`) |
| Render | Microsoft Graph | `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET`, `ONEDRIVE_SOURCES`, `EMAIL_PROVIDER` |
| Render | Perimeter / admin | `ORIGIN_SECRET`, `BOOTSTRAP_ADMIN_EMAIL` |
| Vercel | Perimeter | `ORIGIN_SECRET` (**the same value as on Render**) |

Generate an origin secret with `openssl rand -hex 32` (or any long random
string). When `ORIGIN_SECRET` is set, every backend path except `/health`
requires the matching `x-origin-secret` header and otherwise returns
`403 OriginNotAllowed`. This blocks direct calls to the Render URL that bypass
Vercel. It is a shared secret between proxy and backend, **not user
authentication**.

### Startup behaviour

On start the backend configures logging, promotes a bootstrap admin if none
exists, and starts the OneDrive and digest schedulers **only if** enabled
(both off in `render.yaml`).

### Verifying a deployment

1. Render health check passes (`/health`).
2. `https://ai-shadow-mvp.vercel.app/` loads and the **Current Digital Twin**
   list populates. This proves the Vercel → Render route and the origin secret
   both work.
3. Ask a Chat question about a known document and confirm sources.
4. Optional end-to-end report check from a workstation:
   `python -m scripts.smoke_activity_report --base-url <backend-url> --user-email <email> --start <YYYY-MM-DD> --end <YYYY-MM-DD>`.

### Common deployment problems

| Problem | Cause | Resolution |
|---|---|---|
| Every API call returns 403 `OriginNotAllowed` | `ORIGIN_SECRET` differs between Vercel and Render, or is missing on Vercel | Set the identical value in both, then redeploy both |
| Frontend loads but the API returns 404/502 | Render service renamed or asleep; `vercel.json` hostname wrong | Check the Render service URL; update `vercel.json` |
| CORS errors in the browser | The frontend is calling the backend origin directly | Use the `/api` proxy; the backend has no CORS support |
| Pre-deploy fails | Database unreachable or a migration error | Check `DATABASE_URL`, Neon status and SSL settings |
| `ModuleNotFoundError: psycopg` at startup | Outdated requirements | Redeploy with the current `requirements.txt` |
| Chat or reports return 502 | Provider key missing, out of credit, or rate limited | Check `OPENROUTER_API_KEY` and the OpenRouter balance |
| Inbox shows **Connection failed** | Mail permissions not granted or mailbox address wrong | Grant `Mail.Read`/`Mail.Send` with admin consent; fix the address |

---

## 13. Security, privacy and limitations

Read this before putting sensitive information into AI Shadow.

**Authentication.** There is **no sign-in**. The selected twin's id travels
in the `X-User-ID` header and is trusted exactly as sent. Anyone who can reach
the application can select any twin and see that twin's private data
(profile, memories, drafts, tasks, reports, mailbox contents). The
`ORIGIN_SECRET` check only ensures requests come through the Vercel proxy.
**Do not treat AI Shadow as access-controlled** until real authentication
exists.

**What is isolated.** Private data is stored per twin and every query filters
on the twin's id, so selecting one twin never loads another's data. The
Knowledge Base is intentionally shared by all.

**Company documents.** Uploaded and synchronised documents are stored in the
database. Passages are sent to the configured embedding and language-model
provider (OpenRouter by default, which routes to third-party model hosts) to
create embeddings and answers. Only upload documents your organisation allows
to be processed by that provider.

**Email.** Triage and drafting send message content, the twin's profile and,
optionally, Knowledge Base passages to the model provider. Triage results are
stored; message bodies are not. Draft attachments are stored in the database.

**Microsoft Graph permissions.** Application permissions are **tenant-wide**:
`Mail.Send` lets the application send as any mailbox it can reach, and
`Files.Read.All` reads any file in the tenant. AI Shadow limits itself to each
twin's configured mailbox and the configured OneDrive sources, but the
permission grant itself is broad. Grant only what you use. At the time of
writing, `Calendars.Read` has not been granted in production.

**Human review.** AI answers, triage results, drafts and reports can be wrong,
incomplete or out of date. Sources show what the model was given, not proof
that every statement is correct. Every outgoing email requires explicit
approval and a separate **Send**.

**Limits and transient failures.**

- Each model call is capped at **4,096 output tokens**, with a 45-second
  timeout and one retry.
- OpenRouter can reject requests when account credit is low or too many
  requests run at once (HTTP 402, for example `in_flight_budget_exhausted`).
  The token cap reduces this but cannot prevent it when several requests (for
  example report generation and chat) run concurrently.
- Activity reports get 90 seconds of total model time and fall back to a
  structural report when the budget is exhausted.

**Not production-hardened.**

- No authentication or role-based access (beyond a single admin flag used for
  deletion).
- Chat history exists only in the browser.
- No OCR; no legacy Office formats.
- Inbox paging tops out at 200 messages; received attachments cannot be
  downloaded.
- A live mailbox and calendar have had limited real-world validation (see
  `docs/KNOWN_ISSUES.md`).
- Background schedulers run per process and are off in production.
- No CI pipeline runs the tests automatically.

---

## 14. Troubleshooting and FAQ

| Symptom | Likely cause | Resolution |
|---|---|---|
| Chat says *"I could not find anything in your documents…"* | No relevant indexed passage, or the Knowledge Base is empty | Check **Knowledge Base** for the document and its status; rephrase with document terms |
| Chat: *"The language model provider is unavailable."* (502) | Provider error, timeout, missing key or no credit | Retry; administrator checks `OPENROUTER_API_KEY`, credit and logs (`llm_call_failed`) |
| OpenRouter **402** / `in_flight_budget_exhausted` | Low account credit or too many concurrent requests | Wait and retry; avoid running several reports at once; top up credit |
| Report shows *"Structural analysis only"* | Model unavailable or 90-second budget exhausted | **Regenerate** later |
| Report: *"The server failed to generate this report (HTTP N)"* | Mailbox read or model failure | Check the mailbox connection; retry; check server logs |
| *"No mailbox is connected"* / HTTP 409 | No mailbox for this twin, or `EMAIL_PROVIDER` unset | Connect the mailbox on **Reports**; administrator sets `EMAIL_PROVIDER=outlook` |
| Mailbox **Connection failed** | Wrong address, mailbox outside the tenant, or missing `Mail.Read`/`Mail.Send` consent | Correct the address; grant admin consent |
| *"The reply could not be drafted."* | Model failure or mailbox read failure | Retry; draft manually in **Compose** |
| **Send** disabled | Missing recipient, subject or message, or not approved | Follow the hints under the buttons; click **Approve for sending** |
| Document **Unsupported** | Legacy or unsupported format, or too large (OneDrive) | Convert to PDF/DOCX/PPTX/XLSX; keep files under 10 MB |
| Document **Needs OCR** | Scanned PDF | Provide a text-based version |
| Document **Failed** | Parse or embedding error (message shown under the filename) | Fix and re-upload; administrator may run `scripts.backfill_embeddings` |
| OneDrive **Not configured** | No Graph credentials on the server | Configure per [11.11](#1111-optional-microsoft-graph-onedrive-mail-calendar) |
| OneDrive **Failed** / `access_denied` | `Files.Read.All` not consented, or folder outside the tenant | Grant admin consent; use a tenant SharePoint/OneDrive link |
| OneDrive **Partial** | Some files failed to download or parse | Check source details and **Failed** documents; run **Sync now** again |
| Calendar import fails | `Calendars.Read` not granted | Grant the permission with admin consent |
| Deleting a twin returns 403 | The selected twin is not the administrator | Select the admin twin first (see `BOOTSTRAP_ADMIN_EMAIL`) |
| Database connection or migration errors | Wrong `DATABASE_URL`, database down, or pgvector missing | See [11.13](#1113-common-installation-and-runtime-errors) |
| Frontend cannot reach the backend | Backend down, wrong proxy target, or origin secret mismatch | Locally: start uvicorn and check `VITE_BACKEND_ORIGIN`; in production: check Render health and `ORIGIN_SECRET` |

**FAQ**

- **Is my chat history saved?** Only in your browser. Other people and other
  devices cannot see it.
- **Does the twin learn from my conversations?** No. It only uses what is on
  its profile and memories.
- **Can the assistant send an email by itself?** No. Every send needs a person
  to approve and then press **Send**.
- **Why can't my twin answer about a document I uploaded?** Check that its
  status is **indexed**. Scanned, unsupported or failed documents are not
  searchable.
- **Are my documents private to me?** No. The Knowledge Base is shared by every
  twin.

---

## 15. Repository structure and contributing

```text
ai-shadow-mvp/
├── README.md                 This guide
├── CLAUDE.md                 How work is done here: workflow, standards, rules
├── .env.example              Backend configuration template (copy to backend/.env)
├── docker-compose.yml        Local PostgreSQL 16 + pgvector
├── render.yaml               Render Blueprint (backend service + database)
├── docs/
│   ├── PROJECT_STATE.md      Current checkpoint — read first
│   ├── FEATURES.md           What exists today (authoritative)
│   ├── ARCHITECTURE.md       System design
│   ├── DECISIONS.md          Why the codebase is shaped this way
│   ├── KNOWN_ISSUES.md       Gaps, limitations, accepted trade-offs
│   └── ROADMAP.md            What is next
├── backend/
│   ├── app/
│   │   ├── main.py           App, middleware, error mapping, schedulers, /health
│   │   ├── api/              Route modules (chat, documents, email, reports, …)
│   │   ├── config/           Settings (pydantic-settings)
│   │   ├── core/             Constants and domain exceptions
│   │   ├── database/         Engine, session, vector type
│   │   ├── models/           SQLAlchemy models
│   │   ├── prompts/          Registered prompt templates
│   │   ├── schemas/          Pydantic request/response models
│   │   └── services/         features/, engines/, email/, graph/, llm/
│   ├── alembic/              Migrations 0001–0016
│   ├── scripts/              Operational scripts (python -m scripts.<name>)
│   ├── tests/                Pytest suite mirroring app/
│   ├── requirements.txt
│   ├── pyproject.toml        Ruff configuration
│   └── pytest.ini
└── frontend/
    ├── src/
    │   ├── api/              API client and types
    │   ├── components/       chat, twins, knowledge, email, tasks, reports, ui
    │   ├── lib/              Pure UI logic (conversations, uploads, email, …)
    │   ├── state/            Current-twin context
    │   └── tests/            Node test runner suites
    ├── vite.config.ts        Dev server and /api proxy
    ├── vercel.json           Vercel build and /api rewrite
    ├── .env.example          VITE_API_BASE_URL, VITE_BACKEND_ORIGIN
    └── package.json
```

**Useful scripts** (run from `backend/` as `python -m scripts.<name>`):
`verify_embedding_provider`, `backfill_embeddings`, `discover_onedrive_sources`,
`verify_graph_access`, `diagnose_onedrive_corpus`, `validate_live_integration`,
`diagnose_activity_report`, `smoke_activity_report`, `static_check`.

### Making a change

1. Read `docs/PROJECT_STATE.md` and the relevant part of `docs/FEATURES.md`.
   Follow [`CLAUDE.md`](CLAUDE.md).
2. Create a branch (`feature/*`, `bugfix/*`, `refactor/*`, `docs/*`).
3. Make the smallest change that solves the problem, following existing
   patterns: prompts in `app/prompts/system.py`, domain errors in
   `app/core/exceptions.py` mapped in `app/main.py`.
4. Add or update tests. Tests must not make network calls or need credentials.
5. Run the focused tests, then the checks:

   ```powershell
   cd backend;  pytest <path> -q;  ruff check app tests alembic;  ruff format --check app tests alembic
   cd ..\frontend;  npm test;  npm run lint;  npm run typecheck
   ```

6. Update the owning document in `docs/` in the same change.
7. Open a pull request with one concern per PR. Never commit `.env` or any
   secret, and never commit collected mailbox data (`backend/report_data*/`).
