/**
 * Filtering the document list, and describing OneDrive source status.
 *
 * Corpus totals are deliberately *not* computed here. They come from
 * `GET /documents/stats`, counted in the database: this module once derived
 * them from whichever page of documents happened to be loaded, which was a
 * sample presented as a total.
 */

import type {
  DocumentStatus,
  DocumentSummary,
  SyncSourceState,
  SyncStatusResponse,
} from "../api/types";

export function matchesQuery(document: DocumentSummary, query: string): boolean {
  const needle = query.trim().toLowerCase();

  if (!needle) return true;

  return document.filename.toLowerCase().includes(needle);
}

export function filterDocuments(
  documents: DocumentSummary[],
  options: { query?: string; status?: DocumentStatus | "all" } = {},
): DocumentSummary[] {
  const { query = "", status = "all" } = options;

  return documents.filter(
    (document) =>
      matchesQuery(document, query) && (status === "all" || document.status === status),
  );
}

// --- OneDrive source status ----------------------------------------------
//
// The presentation decisions live here rather than in the panel so that they
// can be asserted without rendering. What a person needs from this screen is
// one sentence per folder — is it synced, when, and if not, why — and every
// one of those sentences is a pure function of what the backend reported.
//
// Nothing here computes a status. The backend is the only thing that decides
// whether a source is `succeeded` or `partial`, for the same reason task
// colours are decided server-side: one threshold, one place.

export type Tone = "positive" | "neutral" | "warning" | "danger" | "info";

const STATUS_LABELS: Record<SyncSourceState["status"], { label: string; tone: Tone }> =
  {
    never_run: { label: "Not yet synced", tone: "neutral" },
    running: { label: "Syncing", tone: "info" },
    succeeded: { label: "Synced", tone: "positive" },
    // Everything reachable was processed but something transient failed, so the
    // backend deliberately held its delta token back. Not a failure.
    partial: { label: "Partial", tone: "warning" },
    failed: { label: "Failed", tone: "danger" },
  };

export interface SourcePresentation {
  key: string;
  name: string;
  label: string;
  tone: Tone;
  detail: string | null;
  location: string | null;
  discovered: number;
  indexed: number;
  replaced: number;
  deleted: number;
  unsupported: number;
  failed: number;
  lastSucceededAt: string | null;
  lastAttemptedAt: string | null;
}

export function describeSyncSource(source: SyncSourceState): SourcePresentation {
  const presentation = STATUS_LABELS[source.status];

  // A disabled or de-configured source is not "synced", whatever its last run
  // said — reporting the stale success would be the screen lying quietly.
  const label = !source.configured
    ? "No longer configured"
    : !source.enabled
      ? "Disabled"
      : presentation.label;

  const tone: Tone =
    !source.configured || !source.enabled ? "neutral" : presentation.tone;

  return {
    key: source.source_key,
    name: source.label ?? source.source_key,
    label,
    tone,
    // The backend's message when there is one; otherwise nothing invented.
    detail: source.error_message,
    location: source.path ?? null,
    discovered: source.last_discovered,
    indexed: source.last_indexed,
    replaced: source.last_replaced,
    deleted: source.last_deleted,
    unsupported: source.last_unsupported,
    failed: source.last_failed,
    lastSucceededAt: source.last_succeeded_at,
    lastAttemptedAt: source.last_attempted_at,
  };
}

export interface SyncTotals {
  sources: number;
  enabled: number;
  synced: number;
  failing: number;
  unsupported: number;
  failed: number;
  lastSyncedAt: string | null;
}

export function syncTotals(status: SyncStatusResponse): SyncTotals {
  const sources = status.sources;

  const timestamps = sources
    .map((source) => source.last_succeeded_at)
    .filter((value): value is string => Boolean(value))
    .sort();

  return {
    sources: sources.length,
    enabled: sources.filter((source) => source.enabled && source.configured).length,
    synced: sources.filter((source) => source.status === "succeeded").length,
    failing: sources.filter(
      (source) => source.status === "failed" || source.status === "partial",
    ).length,
    unsupported: sources.reduce((total, source) => total + source.last_unsupported, 0),
    failed: sources.reduce((total, source) => total + source.last_failed, 0),
    // The oldest success, not the newest: the corpus is only as current as its
    // most stale folder, and showing the newest would flatter a broken source.
    lastSyncedAt:
      timestamps.length === sources.length && timestamps.length > 0
        ? timestamps[0]
        : null,
  };
}
