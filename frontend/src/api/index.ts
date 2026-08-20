/**
 * Named calls, one per backend endpoint. Paths are written out in full so
 * they can be grepped against `backend/app/api/` in one pass.
 */

import { request, upload } from "./client";
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
  EmailTemplate,
  EmailTemplateCreate,
  EmailTemplateFilled,
  EmailTemplateList,
  EmailTemplateUpdate,
  EmailThreadSummary,
  TriagedMessage,
  BatchUploadResponse,
  ChatResponse,
  DocumentList,
  DocumentStatus,
  DocumentSummary,
  Memory,
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

export const listUsers = () => request<UserList>("/users");

export const createUser = (body: UserCreate) =>
  request<User>("/users", { method: "POST", body });

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
    method: "POST",
    body: { question, top_k: options.topK ?? null },
    signal: options.signal,
  });

// --- documents (backend/app/api/document_routes.py) ----------------------

export const listDocuments = (
  options: { status?: DocumentStatus; limit?: number; offset?: number } = {},
) =>
  request<DocumentList>("/documents", {
    query: {
      status: options.status,
      limit: options.limit ?? 50,
      offset: options.offset ?? 0,
    },
  });

export const getDocument = (id: string) => request<DocumentSummary>(`/documents/${id}`);

export const deleteDocument = (id: string) =>
  request<void>(`/documents/${id}`, { method: "DELETE" });

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

export const getSyncStatus = () => request<SyncStatusResponse>("/sync/onedrive/status");

export const runSync = (options: { source?: string; full?: boolean } = {}) =>
  request<SyncRunResponse>("/sync/onedrive", {
    method: "POST",
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
) => request<EmailTemplate>(`/email/templates/${id}`, { method: "PATCH", body, userId });

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

export const getEmailProviderStatus = () =>
  request<EmailProviderStatus>("/email/provider/status");

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
