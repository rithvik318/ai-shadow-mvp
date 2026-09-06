/**
 * Named calls, one per backend endpoint. Paths are written out in full so
 * they can be grepped against `backend/app/api/` in one pass.
 */

import { download, request, upload } from "./client";
import type {
  ComposeRequest,
  ComposeResponse,
  EmailAssessment,
  EmailAssessmentList,
  EmailAttachment,
  EmailCategory,
  EmailDraft,
  EmailDraftCreate,
  EmailDraftList,
  EmailDraftStatus,
  EmailDraftUpdate,
  EmailInbox,
  EmailMessageInput,
  EmailProviderStatus,
  Mailbox,
  MailboxUpdate,
  ReportEnvelope,
  ReportHistory,
  ReportType,
  EmailTemplate,
  EmailTemplateCreate,
  EmailTemplateFilled,
  EmailTemplateList,
  EmailTemplateUpdate,
  EmailThreadSummary,
  TriagedMessage,
  BatchUploadResponse,
  ChatResponse,
  CalendarEvent,
  CorpusStats,
  DocumentList,
  DocumentStatus,
  DocumentSummary,
  EventCreate,
  EventList,
  EventStatus,
  Memory,
  Task,
  TaskCreate,
  TaskList,
  TaskStatus,
  TaskUpdate,
  UserDeletionPreview,
  UserDeletionResult,
  WeeklyReport,
  MemoryCreate,
  MemoryList,
  MemoryType,
  Profile,
  ProfileUpdate,
  MemoryUpdate,
  SearchResponse,
  SyncRunResponse,
  SyncStatusResponse,
  User,
  UserCreate,
  UserList,
} from "./types";

// --- users (backend/app/api/user_routes.py) ------------------------------

// The user registry and the shared corpus send no identity: `userId: null`
// is a decision, not an omission. `/documents`, `/search` and `/sync` are
// company-wide, and a header there would imply a per-user corpus that does
// not exist; `/users` is how the workspace discovers who it could act as, so
// it cannot require having already chosen.
export const listUsers = () => request<UserList>("/users", { userId: null });

export const createUser = (body: UserCreate) =>
  request<User>("/users", { method: "POST", body, userId: null });

// --- chat (backend/app/api/chat_routes.py) -------------------------------

/** `/chat` requires `X-User-ID`: it loads that user's Digital Twin. */
export const askQuestion = (
  question: string,
  userId: string,
  options: { topK?: number; signal?: AbortSignal } = {},
) =>
  request<ChatResponse>("/chat", {
    method: "POST",
    body: { question, top_k: options.topK ?? null },
    userId,
    signal: options.signal,
  });

// --- search (backend/app/api/search_routes.py) ---------------------------

/** Retrieval only, no model call. Not user-scoped — the corpus is shared. */
export const searchDocuments = (
  question: string,
  options: { topK?: number; signal?: AbortSignal } = {},
) =>
  request<SearchResponse>("/search", {
    userId: null,
    method: "POST",
    body: { question, top_k: options.topK ?? null },
    signal: options.signal,
  });

// --- documents (backend/app/api/document_routes.py) ----------------------

export const listDocuments = (
  options: { status?: DocumentStatus; limit?: number; offset?: number } = {},
) =>
  request<DocumentList>("/documents", {
    userId: null,
    query: {
      status: options.status,
      limit: options.limit ?? 50,
      offset: options.offset ?? 0,
    },
  });

/**
 * Corpus totals. Separate from `listDocuments` because a page of documents is
 * a sample and this is a count.
 */
export const getCorpusStats = () =>
  request<CorpusStats>("/documents/stats", { userId: null });

export const getDocument = (id: string) =>
  request<DocumentSummary>(`/documents/${id}`, { userId: null });

export const deleteDocument = (id: string) =>
  request<void>(`/documents/${id}`, { method: "DELETE", userId: null });

/**
 * The batch endpoint, always — including for one file.
 *
 * `/documents/upload` returns a document and raises 415 for an unsupported
 * type; `/documents/batch-upload` returns a per-file result instead. Using
 * one path for both means the UI has one result shape to render, and a
 * rejected file is a row in the tray rather than a thrown error.
 */
export const uploadDocuments = (files: File[], signal?: AbortSignal) => {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);

  return upload<BatchUploadResponse>("/documents/batch-upload", form, {
    userId: null,
    signal,
  });
};

// --- digital twin (profile_routes.py, memory_routes.py) ------------------

export const getProfile = (userId: string) => request<Profile>("/profile", { userId });

export const saveProfile = (userId: string, body: ProfileUpdate) =>
  request<Profile>("/profile", { method: "PUT", body, userId });

export const listMemories = (
  userId: string,
  options: { type?: MemoryType; active?: boolean } = {},
) =>
  request<MemoryList>("/memory", {
    userId,
    query: { type: options.type, active: options.active },
  });

export const createMemory = (userId: string, body: MemoryCreate) =>
  request<Memory>("/memory", { method: "POST", body, userId });

export const updateMemory = (userId: string, id: string, body: MemoryUpdate) =>
  request<Memory>(`/memory/${id}`, { method: "PATCH", body, userId });

export const deleteMemory = (userId: string, id: string) =>
  request<void>(`/memory/${id}`, { method: "DELETE", userId });

// --- OneDrive sync (backend/app/api/sync_routes.py) ----------------------
//
// Not user-scoped: the knowledge base is shared, so no X-User-ID is sent.

export const getSyncStatus = () =>
  request<SyncStatusResponse>("/sync/onedrive/status", { userId: null });

export const runSync = (options: { source?: string; full?: boolean } = {}) =>
  request<SyncRunResponse>("/sync/onedrive", {
    method: "POST",
    userId: null,
    body: { source: options.source ?? null, full: options.full ?? false },
  });

// --- Email Agent: templates (backend/app/api/email_template_routes.py) ---
//
// Every call sends X-User-ID: templates, drafts and assessments are private to
// the twin acting, exactly as profile and memory are.

export const listEmailTemplates = (userId: string) =>
  request<EmailTemplateList>("/email/templates", { userId });

export const createEmailTemplate = (userId: string, body: EmailTemplateCreate) =>
  request<EmailTemplate>("/email/templates", { method: "POST", body, userId });

export const updateEmailTemplate = (
  userId: string,
  id: string,
  body: EmailTemplateUpdate,
) =>
  request<EmailTemplate>(`/email/templates/${id}`, { method: "PATCH", body, userId });

export const deleteEmailTemplate = (userId: string, id: string) =>
  request<void>(`/email/templates/${id}`, { method: "DELETE", userId });

/**
 * Substitution only — no model call. Unfilled placeholders come back visible
 * as `{{ name }}` and are listed in `missing`, so a person can see what they
 * still owe rather than finding a blank where a client's name should be.
 */
export const fillEmailTemplate = (
  userId: string,
  id: string,
  values: Record<string, string>,
) =>
  request<EmailTemplateFilled>(`/email/templates/${id}/fill`, {
    method: "POST",
    body: { values },
    userId,
  });

// --- Email Agent: drafts (backend/app/api/email_draft_routes.py) ---------

export const listEmailDrafts = (
  userId: string,
  options: { status?: EmailDraftStatus } = {},
) =>
  request<EmailDraftList>("/email/drafts", {
    userId,
    query: { status: options.status },
  });

export const getEmailDraft = (userId: string, id: string) =>
  request<EmailDraft>(`/email/drafts/${id}`, { userId });

export const createEmailDraft = (userId: string, body: EmailDraftCreate) =>
  request<EmailDraft>("/email/drafts", { method: "POST", body, userId });

export const updateEmailDraft = (userId: string, id: string, body: EmailDraftUpdate) =>
  request<EmailDraft>(`/email/drafts/${id}`, { method: "PATCH", body, userId });

export const deleteEmailDraft = (userId: string, id: string) =>
  request<void>(`/email/drafts/${id}`, { method: "DELETE", userId });

export const attachToEmailDraft = (userId: string, id: string, file: File) => {
  const form = new FormData();
  form.append("file", file, file.name);

  return upload<EmailAttachment>(`/email/drafts/${id}/attachments`, form, { userId });
};

export const removeEmailAttachment = (
  userId: string,
  draftId: string,
  attachmentId: string,
) =>
  request<void>(`/email/drafts/${draftId}/attachments/${attachmentId}`, {
    method: "DELETE",
    userId,
  });

/** Records that a person read this draft. Does not send it. */
export const approveEmailDraft = (userId: string, id: string) =>
  request<EmailDraft>(`/email/drafts/${id}/approve`, { method: "POST", userId });

/**
 * Sends an already-approved draft. `409` when the draft is not approved or no
 * mailbox is connected; `502` when the provider refused. There is no path that
 * reports a send the provider did not confirm.
 */
export const sendEmailDraft = (userId: string, id: string) =>
  request<EmailDraft>(`/email/drafts/${id}/send`, {
    method: "POST",
    body: { confirm: true },
    userId,
  });

// --- Email Agent: generation, mailbox, triage (email_routes.py) ----------

/**
 * Whether *this* user's mailbox is reachable.
 *
 * The user is named explicitly rather than left to the ambient identity. Both
 * resolve to the same person today, but a call that states whose answer it
 * wants cannot be misattributed when one is in flight as somebody switches —
 * and the endpoint is per-user, so the identity is part of the question rather
 * than a header the client happens to add.
 */
export const getEmailProviderStatus = (userId?: string) =>
  request<EmailProviderStatus>("/email/provider/status", { userId });

export const composeEmail = (userId: string, body: ComposeRequest) =>
  request<ComposeResponse>("/email/compose", { method: "POST", body, userId });

export const listEmailMessages = (userId: string, options: { limit?: number } = {}) =>
  request<EmailInbox>("/email/messages", {
    userId,
    query: { limit: options.limit ?? 25 },
  });

export const getEmailMessage = (userId: string, messageId: string) =>
  request<TriagedMessage>(`/email/messages/${encodeURIComponent(messageId)}`, {
    userId,
  });

export const triageEmail = (
  userId: string,
  body: { message_id?: string; message?: EmailMessageInput; persist?: boolean },
) => request<EmailAssessment>("/email/triage", { method: "POST", body, userId });

export const summarizeEmailThread = (
  userId: string,
  body: { thread_id?: string; messages?: EmailMessageInput[] },
) =>
  request<EmailThreadSummary>("/email/threads/summarize", {
    method: "POST",
    body,
    userId,
  });

export const listEmailAssessments = (
  userId: string,
  options: { category?: EmailCategory } = {},
) =>
  request<EmailAssessmentList>("/email/assessments", {
    userId,
    query: { category: options.category },
  });

export const listEmailFollowUps = (
  userId: string,
  options: { includeHandled?: boolean } = {},
) =>
  request<EmailAssessmentList>("/email/follow-ups", {
    userId,
    query: { include_handled: options.includeHandled ?? false },
  });

export const setEmailFollowUpHandled = (
  userId: string,
  assessmentId: string,
  handled: boolean,
) =>
  request<EmailAssessment>(`/email/follow-ups/${assessmentId}/handled`, {
    method: "POST",
    body: { handled },
    userId,
  });

// --- tasks, events and the weekly report (backend/app/api/task_routes.py) -
//
// Every one is user-scoped: tasks, events and the report are private, exactly
// as profile and memory are.

export const listTasks = (userId: string, options: { status?: TaskStatus } = {}) =>
  request<TaskList>("/tasks", { userId, query: { task_status: options.status } });

export const createTask = (userId: string, body: TaskCreate) =>
  request<Task>("/tasks", { method: "POST", body, userId });

export const updateTask = (userId: string, id: string, body: TaskUpdate) =>
  request<Task>(`/tasks/${id}`, { method: "PATCH", body, userId });

export const completeTask = (userId: string, id: string) =>
  request<Task>(`/tasks/${id}/complete`, { method: "POST", userId });

export const deleteTask = (userId: string, id: string) =>
  request<void>(`/tasks/${id}`, { method: "DELETE", userId });

export const tasksFromFollowUps = (userId: string) =>
  request<TaskList>("/tasks/from-follow-ups", { method: "POST", userId });

/**
 * Add one triaged email to this user's tasks, and get the task back.
 *
 * The bulk sweep above answers "make tasks for everything", which tells a
 * person nothing about the one message they were looking at. This returns the
 * task so the UI can show it. Pressing it twice returns the same task —
 * deduplication is the server's, keyed on the message.
 */
export const taskFromFollowUp = (userId: string, assessmentId: string) =>
  request<Task>(`/tasks/from-follow-up/${assessmentId}`, {
    method: "POST",
    userId,
  });

export const getWeeklyReport = (userId: string) =>
  request<WeeklyReport>("/reports/weekly", { userId });

export const listEvents = (userId: string) => request<EventList>("/events", { userId });

export const createEvent = (userId: string, body: EventCreate) =>
  request<CalendarEvent>("/events", { method: "POST", body, userId });

/**
 * Record what a person says happened. The only route by which an event
 * becomes attended or missed — nothing infers it from a meeting existing.
 * `unknown` withdraws an answer.
 */
export const markAttendance = (
  userId: string,
  id: string,
  status: EventStatus,
  note?: string,
) =>
  request<CalendarEvent>(`/events/${id}/attendance`, {
    method: "POST",
    body: { status, note: note ?? null },
    userId,
  });

export const deleteEvent = (userId: string, id: string) =>
  request<void>(`/events/${id}`, { method: "DELETE", userId });

// --- user deletion (backend/app/api/user_routes.py) ----------------------
//
// Admin-only on the server, and both calls name the user in the path rather
// than taking them from the header: an administrator is acting *on* somebody
// else, which is the one place a user id legitimately travels in a URL.

export const previewUserDeletion = (adminId: string, userId: string) =>
  request<UserDeletionPreview>(`/users/${userId}/deletion-preview`, {
    userId: adminId,
  });

export const deleteUser = (adminId: string, userId: string) =>
  request<UserDeletionResult>(`/users/${userId}`, {
    method: "DELETE",
    userId: adminId,
  });

// --- reports and digests (backend/app/api/report_routes.py) --------------
//
// User-scoped like every other report call. `period` is omitted for the
// period in progress; a key that is not a period the server can name comes
// back as a 400 rather than being quietly swapped for the current one.

export const getReport = (
  userId: string,
  options: { reportType?: ReportType; period?: string; refresh?: boolean } = {},
) =>
  request<ReportEnvelope>("/reports", {
    userId,
    query: {
      report_type: options.reportType,
      period: options.period,
      refresh: options.refresh ? "true" : undefined,
    },
  });

export const getReportHistory = (
  userId: string,
  options: { reportType?: ReportType } = {},
) =>
  request<ReportHistory>("/reports/history", {
    userId,
    query: { report_type: options.reportType },
  });

/**
 * Snapshot the caller's last completed week and month of email now, the same
 * way the schedule does. Only ever runs for the caller.
 */
export const runDigests = (userId: string) =>
  request<ReportHistory>("/reports/digests/run", { method: "POST", userId });

/** One stored report as a Word document, with the filename the server chose. */
export const downloadReport = (
  userId: string,
  options: { reportType: ReportType; period: string },
) =>
  download("/reports/document", {
    userId,
    query: { report_type: options.reportType, period: options.period },
  });

// --- the caller's mailbox (backend/app/api/email_routes.py) --------------
//
// Whose mailbox this is comes from the identity header, never from the body.
// There is no call here that names another user, which is why connecting a
// mailbox needs no user id typed into a form.

export const getMailbox = (userId: string) =>
  request<Mailbox>("/email/mailbox", { userId });

export const setMailbox = (userId: string, body: MailboxUpdate) =>
  request<Mailbox>("/email/mailbox", { method: "PUT", body, userId });

export const disconnectMailbox = (userId: string) =>
  request<Mailbox>("/email/mailbox", { method: "DELETE", userId });
