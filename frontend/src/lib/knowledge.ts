/**
 * Knowledge-base statistics, derived from the documents actually returned.
 *
 * The backend exposes no counts endpoint, so every number here is counted
 * from the page in hand rather than invented. `total` comes from the
 * response's own `total`, which is the full corpus size; the per-status
 * counts describe the documents loaded, and the UI says so when they differ.
 */

import type { DocumentStatus, DocumentSummary } from "../api/types";

export interface KnowledgeStats {
  total: number;
  loaded: number;
  byStatus: Record<DocumentStatus, number>;
  complete: boolean;
}

const EMPTY: Record<DocumentStatus, number> = {
  pending: 0,
  processing: 0,
  indexed: 0,
  failed: 0,
  unsupported: 0,
};

export function statsFor(documents: DocumentSummary[], total: number): KnowledgeStats {
  const byStatus = { ...EMPTY };

  for (const document of documents) byStatus[document.status] += 1;

  return {
    total,
    loaded: documents.length,
    byStatus,
    complete: documents.length >= total,
  };
}

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
