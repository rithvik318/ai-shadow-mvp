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
  "pending" | "processing" | "indexed" | "failed" | "unsupported";

/** `backend/app/models/document.py::IngestionResult` */
export type IngestionResult =
  "indexed" | "unchanged" | "replaced" | "failed" | "unsupported";

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

/**
 * Corpus-wide counts (`backend/app/schemas/document_schema.py`).
 *
 * Counted in the database rather than derived from a page of documents, which
 * is the difference between a total and a sample.
 */
export interface CorpusStats {
  documents: number;
  chunks: number;
  embedded_chunks: number;
  by_status: Record<DocumentStatus, number>;
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
  /** The configured folder path, when the source is addressed by path. */
  path: string | null;
  /** The folder's human-facing address. Never a Graph URL. */
  uri: string | null;
  /** Whether this source takes part in a run. */
  enabled: boolean;
  /** False for a source with sync state that is no longer configured. */
  configured: boolean;
  status: SyncStatus;
  has_delta_token: boolean;
  /** Files the last run examined, derived by the backend from the counts. */
  last_discovered: number;
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
  /** Why the configuration could not be read, when it could not be. */
  configuration_error: string | null;
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
  "draft" | "needs_review" | "approved" | "sending" | "sent" | "failed";

export type EmailCategory =
  "urgent" | "needs_reply" | "fyi" | "follow_up" | "low_priority";

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

// --- tasks, events and the weekly report ---------------------------------
// (backend/app/schemas/task_schema.py)

/**
 * The lifecycle a person controls. `overdue` and `escalation_required` are
 * deliberately absent: both are facts about the clock and the rules, true only
 * until the clock moves, so they appear in `display_status` and are never
 * written.
 */
export type TaskStatus = "todo" | "in_progress" | "completed" | "blocked" | "cancelled";

/** `TaskStatus`, plus the two states that are computed on read. */
export type TaskDisplayStatus = TaskStatus | "overdue" | "escalation_required";

/** The severity the backend decided. The frontend maps it to a colour and
 * never recomputes it from a date — one threshold, on the server. */
export type TaskUrgency = "normal" | "warning" | "critical";

export type TaskPriority = "low" | "normal" | "high" | "urgent";

/** What colour the Tasks page paints a row. Decided by the server from
 * completion and the deadline; the frontend maps these four to grey, amber,
 * red and green and computes no dates of its own. */
export type TaskSignal = "todo" | "attention" | "urgent" | "done";

export interface Task {
  id: Uuid;
  title: string;
  description: string | null;

  status: TaskStatus;
  display_status: TaskDisplayStatus;
  urgency: TaskUrgency;
  is_overdue: boolean;
  signal: TaskSignal;
  /** Whole days past the deadline. Null with no deadline, 0 when not late. */
  days_overdue: number | null;
  priority: TaskPriority;

  due_at: IsoDateTime | null;
  completed_at: IsoDateTime | null;

  source: string;
  source_message_id: string | null;
  source_thread_id: string | null;

  contact_name: string | null;
  contact_address: string | null;
  contact_display: string | null;

  /** What a person asked for. Stored. */
  escalation_requested: boolean;
  escalation_note: string | null;
  /** What the rules concluded, right now. Derived, never written back. */
  escalation_required: boolean;
  escalation_reason: string | null;
  escalation_action: string | null;

  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface TaskList {
  items: Task[];
  total: number;
}

export interface TaskCreate {
  title: string;
  description?: string | null;
  priority?: TaskPriority;
  due_at?: IsoDateTime | null;
  contact_name?: string | null;
  contact_address?: string | null;
}

export interface TaskUpdate {
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  priority?: TaskPriority;
  due_at?: IsoDateTime | null;
  escalation_requested?: boolean;
  escalation_note?: string | null;
}

/** What was recorded about a meeting, and how it reads. */
export type EventStatus = "scheduled" | "attended" | "missed" | "cancelled" | "unknown";

/** How an attendance answer was arrived at. `none` means nobody has said. */
export type AttendanceEvidence = "user" | "derived" | "none";

export interface CalendarEvent {
  id: Uuid;
  title: string;
  description: string | null;
  location: string | null;

  starts_at: IsoDateTime;
  ends_at: IsoDateTime | null;

  /** What was recorded. */
  status: EventStatus;
  /** How it reads now: a finished meeting nobody answered for is `unknown`
   * here while staying `scheduled` above. */
  display_status: EventStatus;
  needs_answer: boolean;
  is_past: boolean;

  attendance_evidence: AttendanceEvidence;
  attendance_recorded_at: IsoDateTime | null;
  attendance_note: string | null;

  organiser_name: string | null;
  organiser_address: string | null;

  source: string;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface EventList {
  items: CalendarEvent[];
  total: number;
}

export interface EventCreate {
  title: string;
  starts_at: IsoDateTime;
  ends_at?: IsoDateTime | null;
  location?: string | null;
  description?: string | null;
  /** `"Name <addr>"` or a bare address; split on the way in. */
  organiser?: string | null;
}

/** Where a suggested escalation target came from. */
export type TargetSource = "configured" | "not_identified";

export interface Escalation {
  task: Task;
  reason: string;
  age_seconds: number | null;
  recommended_action: string;
  /** The counterparty the task concerns — usually who has not replied. */
  contact_name: string | null;
  contact_address: string | null;
  /** Who it would be escalated *to*. Never derived from the thread. */
  escalation_contact_name: string | null;
  escalation_contact_address: string | null;
  escalation_contact_display: string;
  target_source: TargetSource;
}

export interface ReportSummary {
  completed_count: number;
  due_soon_count: number;
  overdue_count: number;
  escalation_count: number;
  events_needing_answer_count: number;
}

export interface WeeklyReport {
  generated_at: IsoDateTime;
  period_start: IsoDateTime;
  period_end: IsoDateTime;

  completed_count: number;
  due_soon_count: number;
  overdue_count: number;
  escalation_count: number;
  summary: ReportSummary;

  upcoming: Task[];
  overdue: Task[];
  completed: Task[];
  blocked: Task[];
  follow_ups: Task[];
  high_priority: Task[];
  deadlines: Task[];
  escalations: Escalation[];

  /** Whether a calendar *provider* is connected. Events may still be present —
   * entered by hand — and this says where they did not come from. */
  calendar_connected: boolean;
  calendar_detail: string;
  events: CalendarEvent[];
  events_needing_answer: CalendarEvent[];
}

/** What deleting a user would remove. Read before the confirmation. */
export interface UserDeletionPreview {
  user_id: Uuid;
  name: string;
  email: string;
  /** Row counts per owned table. Counts rather than a generic warning: "14
   * drafts and 60 memories" is a decision somebody can make. */
  owned: Record<string, number>;
  owned_total: number;
  /** Listed to say what is *safe* — the shared corpus is not owned by this
   * person and is not part of the deletion. */
  shared_knowledge_documents: number;
  shared_knowledge_note: string;
}

export interface UserDeletionResult {
  user_id: Uuid;
  deleted: Record<string, number>;
  total: number;
  shared_knowledge_documents: number;
}

// --- reports and email digests (backend/app/api/report_routes.py) --------
//
// Three report types share one envelope. `content` is the report body, and
// which shape it takes is decided by `report_type` — `WeeklyReport` for the
// work report, `EmailDigest` for the two digests, and `{}` when `status` is
// `unavailable`. It is typed as `unknown` rather than as a union because a
// stored snapshot was written by whatever build produced it, and a narrower
// type would make an older row unreadable rather than merely sparse.

export type ReportType =
  | "weekly_work"
  | "weekly_email_digest"
  | "monthly_email_digest";

/** `complete` — produced from real data. `unavailable` — could not be
 * produced, with `detail` saying why. Never zero-filled. */
export type ReportStatus = "complete" | "unavailable";

export type PeriodKind = "week" | "month";

export interface ReportPeriod {
  kind: PeriodKind;
  /** `2026-08-31` for a week (always a Monday), `2026-08` for a month. Sent
   * back verbatim to ask for that period again. */
  key: string;
  label: string;
  start: IsoDateTime;
  /** Exclusive: `start <= t < end`. */
  end: IsoDateTime;
  is_complete: boolean;
}

export interface DigestCorrespondent {
  address: string;
  name: string | null;
  message_count: number;
}

export interface DigestMessage {
  message_id: string;
  subject: string;
  sender_name: string | null;
  sender_address: string | null;
  received_at: IsoDateTime | null;
  /** `untriaged` where nobody has classified this message. Never guessed. */
  category: string;
  priority: string | null;
  summary: string | null;
  needs_reply: boolean;
}

export interface EmailDigest {
  mailbox: string | null;

  received_count: number;
  sent_count: number;
  triaged_count: number;
  untriaged_count: number;
  needs_reply_count: number;
  follow_up_count: number;
  /** Messages the provider returned with no timestamp. Counted in no period
   * rather than swept into this one. */
  undated_count: number;

  by_category: Record<string, number>;
  by_priority: Record<string, number>;
  top_correspondents: DigestCorrespondent[];
  needs_reply: DigestMessage[];
  highlights: DigestMessage[];

  /** The mailbox was read and genuinely held nothing. Different from an
   * `unavailable` report, where no mailbox was read at all. */
  is_quiet: boolean;

  truncated: boolean;
  truncation_detail: string | null;
}

export interface ReportEnvelope {
  report_type: ReportType;
  status: ReportStatus;
  period: ReportPeriod;
  generated_at: IsoDateTime;
  /** Generated over a period that had not finished, so it will be rebuilt on
   * the next request. A final report is read back exactly as written. */
  is_provisional: boolean;
  /** Came from the store rather than from live data — why a past week's
   * report does not move when the work does. */
  from_history: boolean;
  detail: string | null;
  content: unknown;
}

export interface ReportSummaryRow {
  report_type: ReportType;
  status: ReportStatus;
  period: ReportPeriod;
  generated_at: IsoDateTime;
  is_provisional: boolean;
  detail: string | null;
}

export interface ReportHistory {
  report_type: ReportType;
  items: ReportSummaryRow[];
  /** What the calendar offers, not what happens to be stored — so a person
   * can ask for a week nobody has generated yet. */
  available_periods: ReportPeriod[];
  total: number;
}

// --- the caller's mailbox (backend/app/api/email_routes.py) --------------

export interface Mailbox {
  connected: boolean;
  provider: string | null;
  address: string | null;
  display_name: string | null;
  /** The address came from the server's fallback setting rather than from
   * this person. A materially different thing to be looking at. */
  shared_fallback: boolean;
  detail: string | null;
}

export interface MailboxUpdate {
  address: string;
  provider?: string | null;
  display_name?: string | null;
}
