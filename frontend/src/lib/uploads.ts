/**
 * Turning a batch-upload response into something a person can read.
 *
 * Pure functions over the API's shapes, kept out of the components so the
 * mapping from backend vocabulary to UI vocabulary is testable without a DOM.
 */

import type {
  BatchUploadItem,
  BatchUploadResponse,
  IngestionResult,
} from "../api/types";

export type UploadPhase = "selected" | "uploading" | "done";

/** One file's row in the upload tray, from selection to result. */
export interface UploadEntry {
  /** Stable within a tray; the same filename can appear twice in one batch. */
  key: string;
  filename: string;
  sizeBytes: number;
  phase: UploadPhase;
  result?: IngestionResult;
  documentId?: string | null;
  reason?: string | null;
}

export interface ResultPresentation {
  label: string;
  /** What actually happened, for the person who has to act on it. */
  description: string;
  tone: "positive" | "neutral" | "warning" | "danger";
}

/**
 * The five outcomes the backend distinguishes, kept distinct here too.
 *
 * `unchanged` and `replaced` are successes and must not read as failures —
 * they are the ingestion layer's idempotency doing its job. `unsupported` is
 * separated from `failed` because the remedy differs: convert the file, or
 * retry it.
 */
const PRESENTATION: Record<IngestionResult, ResultPresentation> = {
  indexed: {
    label: "Indexed",
    description: "Added to the knowledge base.",
    tone: "positive",
  },
  unchanged: {
    label: "Unchanged",
    description: "Already in the knowledge base; nothing was re-indexed.",
    tone: "neutral",
  },
  replaced: {
    label: "Replaced",
    description: "Content changed, so the previous version was re-indexed.",
    tone: "positive",
  },
  unsupported: {
    label: "Unsupported",
    description: "This file type cannot be read yet.",
    tone: "warning",
  },
  failed: {
    label: "Failed",
    description: "Could not be processed.",
    tone: "danger",
  },
};

export function presentResult(result: IngestionResult): ResultPresentation {
  return PRESENTATION[result];
}

export function entriesFromFiles(files: File[]): UploadEntry[] {
  return files.map((file, index) => ({
    // Index included because two selected files can share a name.
    key: `${index}:${file.name}:${file.size}`,
    filename: file.name,
    sizeBytes: file.size,
    phase: "selected",
  }));
}

/**
 * Match results back onto the entries that produced them.
 *
 * Matched positionally, not by filename: the backend returns one item per
 * submitted file in order, and two files in one batch may share a name — so
 * a lookup by name would attach the wrong outcome to one of them.
 */
export function applyResults(
  entries: UploadEntry[],
  response: BatchUploadResponse,
): UploadEntry[] {
  return entries.map((entry, index) => {
    const item: BatchUploadItem | undefined = response.items[index];

    if (!item) return { ...entry, phase: "done" as const };

    return {
      ...entry,
      phase: "done" as const,
      result: item.result,
      documentId: item.document_id,
      reason: item.reason,
    };
  });
}

/** Did anything land in the knowledge base? Used to decide on a refresh. */
export function changedTheCorpus(response: BatchUploadResponse): boolean {
  return response.items.some(
    (item) => item.result === "indexed" || item.result === "replaced",
  );
}

export function summarise(entries: UploadEntry[]): string {
  const done = entries.filter((entry) => entry.phase === "done");

  if (done.length === 0) return "";

  const counts = new Map<IngestionResult, number>();
  for (const entry of done) {
    if (!entry.result) continue;
    counts.set(entry.result, (counts.get(entry.result) ?? 0) + 1);
  }

  return [...counts.entries()]
    .map(([result, count]) => `${count} ${presentResult(result).label.toLowerCase()}`)
    .join(", ");
}
