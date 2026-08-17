/**
 * Mirrors of the backend's Pydantic response models.
 *
 * Hand-written rather than generated, and deliberately narrow: every type
 * here corresponds to a schema in `backend/app/schemas/`, and a field that
 * does not exist there does not exist here. The point of the layer is that a
 * component cannot read a field the API never sends.
 */

export type Uuid = string;
export type IsoDateTime = string;

/** `backend/app/models/document.py::DocumentStatus` */
export type DocumentStatus =
  | "pending"
  | "processing"
  | "indexed"
  | "failed"
  | "unsupported";

/** `backend/app/models/document.py::IngestionResult` */
export type IngestionResult =
  | "indexed"
  | "unchanged"
  | "replaced"
  | "failed"
  | "unsupported";

/** `backend/app/models/digital_twin.py::MemoryType` */
export type MemoryType = "fact" | "preference" | "decision" | "commitment" | "context";

export const MEMORY_TYPES: readonly MemoryType[] = [
  "fact",
  "preference",
  "decision",
  "commitment",
  "context",
];

// --- users ---------------------------------------------------------------

export interface User {
  id: Uuid;
  name: string;
  email: string;
  role: string;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface UserList {
  items: User[];
  total: number;
}

export interface UserCreate {
  name: string;
  email: string;
  role: string;
}

// --- documents -----------------------------------------------------------

export interface DocumentSummary {
  id: Uuid;
  filename: string;
  content_type: string;
  file_size_bytes: number;
  page_count: number | null;
  chunk_count: number;
  status: DocumentStatus;
  error_message: string | null;
  content_hash: string | null;
  source_uri: string | null;
  source_version: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface DocumentList {
  items: DocumentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface BatchUploadItem {
  filename: string;
  result: IngestionResult;
  succeeded: boolean;
  document_id: Uuid | null;
  status: DocumentStatus | null;
  reason: string | null;
}

export interface BatchUploadResponse {
  items: BatchUploadItem[];
  total: number;
  succeeded: number;
  failed: number;
}

// --- chat and search -----------------------------------------------------

export interface ChatSource {
  document: string;
  section: string | null;
  page: number | null;
  similarity: number;
  document_id: Uuid;
  chunk_id: Uuid;
}

export interface ChatResponse {
  answer: string;
  sources: ChatSource[];
  retrieved_chunks: number;
}

export interface SearchResult extends ChatSource {
  content: string;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  retrieved_count: number;
}

// --- digital twin --------------------------------------------------------

export interface Profile {
  id: Uuid;
  name: string;
  role: string;
  organization: string;
  communication_style: string | null;
  responsibilities: string[];
  expertise: string[];
  priorities: string[];
  decision_preferences: string[];
  current_focus: string[];
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface ProfileUpdate {
  name?: string;
  role?: string;
  organization?: string;
  communication_style?: string;
  responsibilities?: string[];
  expertise?: string[];
  priorities?: string[];
  decision_preferences?: string[];
  current_focus?: string[];
}

export interface Memory {
  id: Uuid;
  type: MemoryType;
  content: string;
  importance: number;
  source: string;
  active: boolean;
  expires_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface MemoryList {
  items: Memory[];
  total: number;
}

export interface MemoryCreate {
  type: MemoryType;
  content: string;
  importance?: number;
}

/** The body every mapped domain error returns — `main.py` builds it. */
export interface ApiErrorBody {
  detail: string;
  error: string;
}

// --- OneDrive synchronisation (backend/app/schemas/sync_schema.py) -------

export type SyncStatus = "never_run" | "running" | "succeeded" | "partial" | "failed";

export interface SyncSourceState {
  source_key: string;
  label: string | null;
  drive_id: string | null;
  item_id: string | null;
  status: SyncStatus;
  has_delta_token: boolean;
  error_message: string | null;
  last_attempted_at: IsoDateTime | null;
  last_succeeded_at: IsoDateTime | null;
  last_duration_ms: number | null;
  last_indexed: number;
  last_replaced: number;
  last_unchanged: number;
  last_deleted: number;
  last_unsupported: number;
  last_failed: number;
}

export interface SyncStatusResponse {
  configured: boolean;
  scheduled: boolean;
  interval_seconds: number | null;
  sources: SyncSourceState[];
}

export interface SyncSourceSummary {
  source_key: string;
  label: string;
  mode: string;
  status: SyncStatus;
  discovered: number;
  indexed: number;
  unchanged: number;
  replaced: number;
  deleted: number;
  unsupported: number;
  failed: number;
  duration_seconds: number;
  delta_advanced: boolean;
  error: string | null;
  files: Array<{
    name: string;
    source_uri: string;
    result: string;
    document_id: Uuid | null;
    reason: string | null;
  }>;
}

export interface SyncRunResponse {
  sources: SyncSourceSummary[];
  total_discovered: number;
  total_indexed: number;
  total_replaced: number;
  total_unchanged: number;
  total_deleted: number;
  total_unsupported: number;
  total_failed: number;
  duration_seconds: number;
}

export interface MemoryUpdate {
  type?: MemoryType;
  content?: string;
  importance?: number;
  active?: boolean;
  expires_at?: IsoDateTime | null;
}
