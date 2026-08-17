import { useCallback, useEffect, useState, type ChangeEvent } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { DocumentStatus, DocumentSummary } from "../../api/types";
import { describeSource, formatBytes, formatDateTime } from "../../lib/format";
import { filterDocuments, statsFor } from "../../lib/knowledge";
import { Badge, Button, EmptyState, ErrorNotice, Spinner } from "../ui";
import { UploadTray } from "../chat/UploadTray";
import { SyncPanel } from "./SyncPanel";

const STATUS_TONE: Record<
  DocumentStatus,
  "positive" | "neutral" | "warning" | "danger" | "info"
> = {
  indexed: "positive",
  pending: "neutral",
  processing: "info",
  // Not failures: the ingestion layer distinguishes "cannot read this format"
  // from "tried and could not finish", and the remedies differ.
  unsupported: "warning",
  failed: "danger",
};

const FILTERS: Array<{ label: string; value: DocumentStatus | "all" }> = [
  { label: "All", value: "all" },
  { label: "Indexed", value: "indexed" },
  { label: "Failed", value: "failed" },
  { label: "Unsupported", value: "unsupported" },
];

export function KnowledgePanel({ reloadToken }: { reloadToken: number }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<DocumentStatus | "all">("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [localReload, setLocalReload] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const page = await api.listDocuments({ limit: 200 });
      setDocuments(page.items);
      setTotal(page.total);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Unable to load the knowledge base. Check that the backend is running.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadToken, localReload]);

  async function remove(document: DocumentSummary) {
    if (!window.confirm(`Delete ${document.filename} from the knowledge base?`)) return;

    setDeleting(document.id);
    setError(null);

    try {
      await api.deleteDocument(document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
      setTotal((current) => Math.max(0, current - 1));
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Could not delete the document.",
      );
    } finally {
      setDeleting(null);
    }
  }

  const stats = statsFor(documents, total);
  const visible = filterDocuments(documents, { query, status });

  return (
    <section className="flex h-full min-w-0 flex-col bg-white">
      <header className="border-b border-ink-200 px-6 py-4">
        <h1 className="text-base font-semibold text-ink-900">Knowledge Base</h1>
        <p className="mt-0.5 text-xs text-ink-500">
          Shared by every Digital Twin. OneDrive synchronisation is the managed source
          for the corpus; manual upload is for ad-hoc documents.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        <div className="space-y-6">
          {/* Counts are derived from the documents actually loaded — the
              backend exposes no aggregate endpoint, so nothing here is a
              number the UI made up. */}
          <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
            <Stat label="Documents" value={stats.total} />
            <Stat label="Indexed" value={stats.byStatus.indexed} />
            <Stat
              label="Processing"
              value={stats.byStatus.processing + stats.byStatus.pending}
            />
            <Stat label="Unsupported" value={stats.byStatus.unsupported} />
            <Stat label="Failed" value={stats.byStatus.failed} />
          </div>
          {!stats.complete && !loading ? (
            <p className="-mt-3 text-xs text-ink-400">
              Counts describe the {stats.loaded} most recent documents of {stats.total}.
            </p>
          ) : null}

          <SyncPanel onSynced={() => setLocalReload((token) => token + 1)} />

          <div>
            <h2 className="text-sm font-semibold text-ink-900">Manual upload</h2>
            <p className="mt-0.5 mb-2.5 text-xs text-ink-500">
              For ad-hoc documents. PDF, DOCX, PPTX, TXT and Markdown.
            </p>
            <UploadTray onIngested={() => setLocalReload((token) => token + 1)} />
          </div>

          <div>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-ink-900">
                Documents{" "}
                <span className="font-normal text-ink-500">({visible.length})</span>
              </h2>
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor="doc-search" className="sr-only">
                  Search documents by filename
                </label>
                <input
                  id="doc-search"
                  type="search"
                  value={query}
                  placeholder="Search filenames…"
                  onChange={(event: ChangeEvent<HTMLInputElement>) =>
                    setQuery(event.target.value)
                  }
                  className="w-52 rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
                />
                {FILTERS.map((filter) => (
                  <button
                    key={filter.value}
                    type="button"
                    aria-pressed={status === filter.value}
                    onClick={() => setStatus(filter.value)}
                    className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                      status === filter.value
                        ? "bg-ink-900 text-white"
                        : "text-ink-600 hover:bg-ink-100"
                    }`}
                  >
                    {filter.label}
                  </button>
                ))}
                <Button type="button" onClick={() => void load()} disabled={loading}>
                  Refresh
                </Button>
              </div>
            </div>

            {error ? <ErrorNotice message={error} onRetry={() => void load()} /> : null}

            {loading ? (
              <div className="py-10 text-center">
                <Spinner label="Loading documents" />
              </div>
            ) : documents.length === 0 && !error ? (
              <EmptyState
                title="No documents yet"
                description="Upload documents above, or configure OneDrive synchronisation to bring in the managed corpus."
              />
            ) : visible.length === 0 ? (
              <p className="rounded-md border border-dashed border-ink-300 px-4 py-8 text-center text-sm text-ink-500">
                No documents match this filter.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-ink-200">
                <table className="w-full min-w-[46rem] text-left text-sm">
                  <thead className="border-b border-ink-200 bg-ink-50/70 text-xs uppercase tracking-wide text-ink-500">
                    <tr>
                      <th scope="col" className="px-4 py-2.5 font-medium">
                        Document
                      </th>
                      <th scope="col" className="px-4 py-2.5 font-medium">
                        Status
                      </th>
                      <th scope="col" className="px-4 py-2.5 font-medium">
                        Source
                      </th>
                      <th scope="col" className="px-4 py-2.5 font-medium">
                        Updated
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-right font-medium">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-200">
                    {visible.map((document) => (
                      <tr key={document.id} className="align-top">
                        <td className="px-4 py-2.5">
                          <p
                            className="max-w-md truncate font-medium text-ink-900"
                            title={document.filename}
                          >
                            {document.filename}
                          </p>
                          <p className="text-xs text-ink-500">
                            {formatBytes(document.file_size_bytes)}
                            {document.chunk_count > 0
                              ? ` · ${document.chunk_count} passages`
                              : ""}
                          </p>
                          {document.error_message ? (
                            <p className="mt-1 max-w-md text-xs text-rose-700">
                              {document.error_message}
                            </p>
                          ) : null}
                        </td>
                        <td className="px-4 py-2.5">
                          <Badge tone={STATUS_TONE[document.status]}>
                            {document.status}
                          </Badge>
                        </td>
                        <td className="px-4 py-2.5 text-ink-600">
                          {describeSource(document.source_uri)}
                        </td>
                        <td className="whitespace-nowrap px-4 py-2.5 text-ink-600">
                          {formatDateTime(document.updated_at)}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <Button
                            variant="danger"
                            type="button"
                            onClick={() => void remove(document)}
                            disabled={deleting === document.id}
                            aria-label={`Delete ${document.filename}`}
                          >
                            {deleting === document.id ? "Deleting…" : "Delete"}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-ink-200 px-4 py-3">
      <p className="text-xl font-semibold tabular-nums text-ink-900">{value}</p>
      <p className="mt-0.5 text-xs text-ink-500">{label}</p>
    </div>
  );
}
