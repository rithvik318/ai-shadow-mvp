/** Small display helpers. Pure, so they can be tested without a browser. */

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;

  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }

  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";

  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";

  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatSimilarity(similarity: number): string {
  if (!Number.isFinite(similarity)) return "—";

  return `${Math.round(similarity * 100)}%`;
}

/**
 * Where a passage sits, in whatever terms its format supports.
 *
 * PDF has pages, PPTX has slides, DOCX has neither and carries a heading
 * instead. The backend puts the ordinal in `page` whichever it means, so this
 * says "Page 4" rather than inventing a distinction the data does not carry.
 */
export function describeLocation(section: string | null, page: number | null): string {
  const parts: string[] = [];

  if (page !== null && page !== undefined) parts.push(`Page ${page}`);
  if (section) parts.push(section);

  return parts.join(" · ");
}

/** Where a document came from, in words rather than a URI scheme. */
export function describeSource(sourceUri: string | null): string {
  if (!sourceUri) return "Uploaded";
  if (sourceUri.startsWith("onedrive:")) return "OneDrive";

  return "External";
}
