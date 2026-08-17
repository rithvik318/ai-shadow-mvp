import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { SyncRunResponse, SyncStatus, SyncStatusResponse } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { Badge, Button, ErrorNotice, Spinner } from "../ui";

/**
 * OneDrive synchronisation, reported from the backend and nothing else.
 *
 * Nothing here is simulated. If the backend says synchronisation is not
 * configured, that is shown as a setup step rather than as a failure — it is
 * the normal state of a deployment that has not been given Microsoft Graph
 * credentials yet. No credential or configuration flow lives in the frontend;
 * that is an environment concern on the server.
 */
const STATUS_PRESENTATION: Record<
  SyncStatus,
  { label: string; tone: "positive" | "neutral" | "warning" | "danger" | "info" }
> = {
  never_run: { label: "Never run", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  succeeded: { label: "Synchronised", tone: "positive" },
  // Everything reachable was processed but something transient failed, so the
  // backend deliberately did not advance its delta token. Not a failure.
  partial: { label: "Partial", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
};

export function SyncPanel({ onSynced }: { onSynced: () => void }) {
  const [status, setStatus] = useState<SyncStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<SyncRunResponse | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      setStatus(await api.getSyncStatus());
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Unable to load sync status.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run() {
    setRunning(true);
    setError(null);
    setLastRun(null);

    try {
      const result = await api.runSync();
      setLastRun(result);
      await load();
      if (result.total_indexed + result.total_replaced + result.total_deleted > 0) {
        onSynced();
      }
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Synchronisation could not be started.",
      );
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="rounded-lg border border-ink-200">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">Knowledge sources</h2>
          <p className="mt-0.5 text-xs text-ink-500">
            OneDrive is the managed source for the SunRadia corpus.
          </p>
        </div>
        {status?.configured ? (
          <Button
            type="button"
            variant="primary"
            onClick={() => void run()}
            disabled={running}
          >
            {running ? "Synchronising…" : "Sync now"}
          </Button>
        ) : null}
      </div>

      <div className="px-4 py-3.5">
        {loading ? (
          <Spinner label="Loading sync status" />
        ) : error ? (
          <ErrorNotice message={error} onRetry={() => void load()} />
        ) : !status?.configured ? (
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-ink-800">OneDrive</span>
              <Badge tone="neutral">Not configured</Badge>
            </div>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-600">
              OneDrive synchronisation is not configured yet. Once SunRadia supplies the
              Microsoft Graph tenant, application credentials and the folders to watch,
              the backend will keep the knowledge base in step automatically — adding
              new files, re-indexing changed ones and removing deleted ones. Until then,
              use manual upload below.
            </p>
            <p className="mt-2 text-xs text-ink-400">
              Configured on the server through <code>ONEDRIVE_*</code> environment
              variables. Nothing is set from this interface.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-ink-500">
              {status.scheduled && status.interval_seconds
                ? `Synchronising automatically every ${Math.round(status.interval_seconds / 60)} minutes.`
                : "Automatic synchronisation is off; runs are manual."}
            </p>

            {status.sources.length === 0 ? (
              <p className="rounded-md border border-dashed border-ink-300 px-4 py-3 text-sm text-ink-500">
                Credentials are present, but no folders are configured to synchronise
                yet.
              </p>
            ) : (
              <ul className="space-y-2">
                {status.sources.map((source) => {
                  const presentation = STATUS_PRESENTATION[source.status];

                  return (
                    <li
                      key={source.source_key}
                      className="rounded-md border border-ink-200 px-3.5 py-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-medium text-ink-900">
                          {source.label ?? source.source_key}
                        </p>
                        <Badge tone={presentation.tone}>{presentation.label}</Badge>
                      </div>
                      <dl className="mt-2 grid gap-x-6 gap-y-1 text-xs text-ink-600 sm:grid-cols-2 lg:grid-cols-4">
                        <Pair
                          label="Last synchronised"
                          value={formatDateTime(source.last_succeeded_at)}
                        />
                        <Pair label="Files added" value={String(source.last_indexed)} />
                        <Pair
                          label="Files updated"
                          value={String(source.last_replaced)}
                        />
                        <Pair
                          label="Files deleted"
                          value={String(source.last_deleted)}
                        />
                      </dl>
                      {source.error_message ? (
                        <p className="mt-1.5 text-xs text-amber-700">
                          {source.error_message}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}

            {lastRun ? (
              <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-sm text-emerald-800">
                {lastRun.total_discovered} file
                {lastRun.total_discovered === 1 ? "" : "s"} examined —{" "}
                {lastRun.total_indexed} added, {lastRun.total_replaced} updated,{" "}
                {lastRun.total_unchanged} unchanged, {lastRun.total_deleted} deleted,{" "}
                {lastRun.total_unsupported} unsupported, {lastRun.total_failed} failed.
              </p>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-ink-400">{label}</dt>
      <dd className="text-ink-800">{value}</dd>
    </div>
  );
}
