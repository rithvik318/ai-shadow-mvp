/**
 * Named calls, one per backend endpoint. Paths are written out in full so
 * they can be grepped against `backend/app/api/` in one pass.
 */

import { request, upload } from "./client";
import type {
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
