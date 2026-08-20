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

// --- Email Agent (backend/app/schemas/email_schema.py) -------------------

export type EmailDraftStatus =
  | "draft"
  | "needs_review"
  | "approved"
  | "sending"
  | "sent"
  | "failed";

export type EmailCategory =
  | "urgent"
  | "needs_reply"
  | "fyi"
  | "follow_up"
  | "low_priority";

export type EmailPriority = "high" | "normal" | "low";

export type EmailTemplateCategory =
  | "introduction"
  | "follow_up"
  | "meeting_request"
  | "proposal_follow_up"
  | "thank_you"
  | "outreach"
  | "custom";

export const EMAIL_TEMPLATE_CATEGORIES: readonly EmailTemplateCategory[] = [
  "introduction",
  "follow_up",
  "meeting_request",
  "proposal_follow_up",
  "thank_you",
  "outreach",
  "custom",
] as const;

/**
 * Every operation `POST /email/compose` accepts. One endpoint, not ten:
 * "shorten" and "make more professional" differ by a sentence of instruction.
 */
export type EmailOperation =
  | "generate"
  | "reply"
  | "rewrite"
  | "improve"
  | "shorten"
  | "expand"
  | "change_tone"
  | "professional"
  | "concise"
  | "subject";

export interface EmailTemplate {
  id: Uuid;
  name: string;
  description: string | null;
  category: EmailTemplateCategory;
  subject_template: string;
  body_template: string;
  /** Derived from the text by the backend, never stored. */
  placeholders: string[];
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface EmailTemplateList {
  items: EmailTemplate[];
  total: number;
}

export interface EmailTemplateCreate {
  name: string;
  subject_template: string;
  body_template: string;
  description?: string | null;
  category?: EmailTemplateCategory;
}

export type EmailTemplateUpdate = Partial<EmailTemplateCreate>;

export interface EmailTemplateFilled {
  subject: string;
  body: string;
  /** Placeholders still unfilled — left visible in the text, not blanked. */
  missing: string[];
}

export interface EmailAttachment {
  id: Uuid;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: IsoDateTime;
}

export interface EmailDraft {
  id: Uuid;
  to_recipients: string[];
  cc_recipients: string[];
  bcc_recipients: string[];
  subject: string;
  body: string;
  status: EmailDraftStatus;
  generated_by_ai: boolean;
  template_id: Uuid | null;
  in_reply_to_message_id: string | null;
  provider: string | null;
  provider_message_id: string | null;
  provider_thread_id: string | null;
  approved_at: IsoDateTime | null;
  sent_at: IsoDateTime | null;
  send_error: string | null;
  attachments: EmailAttachment[];
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface EmailDraftList {
  items: EmailDraft[];
  total: number;
}

export interface EmailDraftCreate {
  to_recipients?: string[];
  cc_recipients?: string[];
  bcc_recipients?: string[];
  subject?: string;
  body?: string;
  template_id?: Uuid | null;
  in_reply_to_message_id?: string | null;
  provider_thread_id?: string | null;
  generated_by_ai?: boolean;
}

export type EmailDraftUpdate = Omit<EmailDraftCreate, "generated_by_ai">;

export interface ComposeRequest {
  operation: EmailOperation;
  instruction?: string | null;
  subject?: string;
  body?: string;
  tone?: string | null;
  recipients?: string[];
  source_subject?: string | null;
  source_body?: string | null;
  source_sender?: string | null;
  use_knowledge_base?: boolean;
}

export interface ComposeSource {
  document: string;
  section: string | null;
  page: number | null;
  similarity: number;
  document_id: Uuid;
  chunk_id: Uuid;
}

export interface ComposeResponse {
  subject: string;
  body: string;
  operation: EmailOperation;
  sources: ComposeSource[];
  /** False means no company knowledge reached the model. */
  knowledge_used: boolean;
  /** False means this twin has no profile yet. */
  persona_used: boolean;
}

export interface EmailProviderStatus {
  provider: string | null;
  configured: boolean;
  connected: boolean;
  mailbox: string | null;
  detail: string | null;
  capabilities: string[];
}

export interface EmailMessageAddress {
  address: string;
  name: string | null;
}

export interface EmailMessageAttachment {
  attachment_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
}

export interface EmailMessage {
  message_id: string;
  thread_id: string | null;
  sender: EmailMessageAddress | null;
  to_recipients: EmailMessageAddress[];
  cc_recipients: EmailMessageAddress[];
  subject: string;
  snippet: string;
  body: string | null;
  received_at: IsoDateTime | null;
  attachments: EmailMessageAttachment[];
  folder: string | null;
  labels: string[];
  is_read: boolean | null;
}

export interface EmailAssessment {
  id: Uuid;
  provider: string;
  provider_message_id: string;
  provider_thread_id: string | null;
  subject: string | null;
  sender: string | null;
  received_at: IsoDateTime | null;
  category: EmailCategory;
  priority: EmailPriority;
  summary: string;
  suggested_action: string | null;
  action_items: string[];
  follow_up_recommended: boolean;
  follow_up_reason: string | null;
  follow_up_due_at: IsoDateTime | null;
  handled: boolean;
  assessed_at: IsoDateTime;
}

export interface EmailAssessmentList {
  items: EmailAssessment[];
  total: number;
}

/** A message with this user's assessment of it, or null if untriaged. */
export interface TriagedMessage {
  message: EmailMessage;
  assessment: EmailAssessment | null;
}

export interface EmailInbox {
  items: TriagedMessage[];
  total: number;
}

/** A message supplied by the caller rather than fetched from a mailbox. */
export interface EmailMessageInput {
  message_id: string;
  thread_id?: string | null;
  sender?: string | null;
  to_recipients?: string[];
  cc_recipients?: string[];
  subject?: string;
  body?: string;
  received_at?: IsoDateTime | null;
}

export interface EmailThreadSummary {
  summary: string;
  action_items: string[];
  suggested_action: string | null;
  follow_up_recommended: boolean;
  follow_up_reason: string | null;
  message_count: number;
}
