import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { SyncRunResponse, SyncStatusResponse } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { describeSyncSource, syncTotals } from "../../lib/knowledge";
import { Badge, Button, ErrorNotice, Spinner } from "../ui";

/**
 * OneDrive synchronisation, reported from the backend and nothing else.
 *
 * Nothing here is simulated. If the backend says synchronisation is not
 * configured, that is shown as a setup step rather than as a failure — it is
 * the normal state of a deployment that has not been given Microsoft Graph
 * credentials yet. No credential or configuration flow lives in the frontend;
 * that is an environment concern on the server.
 *
 * Every status word on this screen came from the backend. The mapping from
 * one to a colour lives in `lib/knowledge.ts` so it can be asserted without
 * rendering, and nothing in this file decides whether a folder is healthy.
 */
export function SyncPanel({ onSynced }: { onSynced: () => void }) {
  const [status, setStatus] = useState<SyncStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<SyncRunResponse | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

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

  async function run(source?: string) {
    setRunning(true);
    setError(null);
    setLastRun(null);

    try {
      const result = await api.runSync(source ? { source } : {});
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

  const totals = status ? syncTotals(status) : null;

  return (
    <div className="rounded-lg border border-ink-200">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">
            Knowledge Base source status
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            OneDrive is the managed source for the SunRadia corpus.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" onClick={() => void load()} disabled={loading}>
            Refresh
          </Button>
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
      </div>

      <div className="px-4 py-3.5">
        {loading ? (
          <Spinner label="Loading sync status" />
        ) : error ? (
          <ErrorNotice message={error} onRetry={() => void load()} />
        ) : status?.configuration_error ? (
          /* Configured wrongly and not configured at all look identical
             unless one of them explains itself. */
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-ink-800">OneDrive</span>
              <Badge tone="danger">Configuration error</Badge>
            </div>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-600">
              {status.configuration_error}
            </p>
            <p className="mt-2 text-xs text-ink-400">
              Corrected on the server in <code>ONEDRIVE_SOURCES</code>. Nothing is set
              from this interface.
            </p>
          </div>
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
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-ink-500">
                {status.scheduled && status.interval_seconds
                  ? `Synchronising automatically every ${Math.round(status.interval_seconds / 60)} minutes.`
                  : "Automatic synchronisation is off; runs are manual."}
              </p>
              {totals ? (
                <p className="text-xs text-ink-500">
                  Last full sync:{" "}
                  <span className="text-ink-800">
                    {formatDateTime(totals.lastSyncedAt)}
                  </span>
                  {totals.unsupported > 0 || totals.failed > 0 ? (
                    <>
                      {" · "}
                      {totals.unsupported} unsupported, {totals.failed} failed
                    </>
                  ) : null}
                </p>
              ) : null}
            </div>

            {status.sources.length === 0 ? (
              <p className="rounded-md border border-dashed border-ink-300 px-4 py-3 text-sm text-ink-500">
                Credentials are present, but no folders are configured to synchronise
                yet.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-ink-200">
                <table className="w-full min-w-[40rem] text-left text-sm">
                  <thead className="border-b border-ink-200 bg-ink-50/70 text-xs uppercase tracking-wide text-ink-500">
                    <tr>
                      <th scope="col" className="px-3.5 py-2 font-medium">
                        Source
                      </th>
                      <th scope="col" className="px-3.5 py-2 font-medium">
                        Status
                      </th>
                      <th scope="col" className="px-3.5 py-2 font-medium">
                        Last synchronised
                      </th>
                      <th scope="col" className="px-3.5 py-2 text-right font-medium">
                        Files
                      </th>
                      <th scope="col" className="px-3.5 py-2 text-right font-medium">
                        Details
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-200">
                    {status.sources.map((state) => {
                      const source = describeSyncSource(state);
                      const open = expanded === source.key;

                      return (
                        <tr key={source.key} className="align-top">
                          <td className="px-3.5 py-2.5">
                            <p className="font-medium text-ink-900">{source.name}</p>
                            {source.location ? (
                              <p
                                className="max-w-sm truncate text-xs text-ink-500"
                                title={source.location}
                              >
                                {source.location}
                              </p>
                            ) : null}
                          </td>
                          <td className="px-3.5 py-2.5">
                            <Badge tone={source.tone}>{source.label}</Badge>
                          </td>
                          <td className="whitespace-nowrap px-3.5 py-2.5 text-ink-600">
                            {formatDateTime(source.lastSucceededAt)}
                          </td>
                          <td className="px-3.5 py-2.5 text-right tabular-nums text-ink-700">
                            {source.discovered}
                          </td>
                          <td className="px-3.5 py-2.5 text-right">
                            <button
                              type="button"
                              aria-expanded={open}
                              onClick={() => setExpanded(open ? null : source.key)}
                              className="text-xs text-ink-600 underline underline-offset-2 hover:text-ink-900"
                            >
                              {open ? "Hide" : "Show"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {status.sources
              .map((state) => describeSyncSource(state))
              .filter((source) => source.key === expanded)
              .map((source) => (
                <div
                  key={source.key}
                  className="rounded-md border border-ink-200 px-3.5 py-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium text-ink-900">{source.name}</p>
                    <Button
                      type="button"
                      onClick={() => void run(source.key)}
                      disabled={running}
                    >
                      {running ? "Synchronising…" : "Sync this source"}
                    </Button>
                  </div>
                  <dl className="mt-2 grid gap-x-6 gap-y-1 text-xs text-ink-600 sm:grid-cols-3 lg:grid-cols-6">
                    <Pair label="Examined" value={String(source.discovered)} />
                    <Pair label="Added" value={String(source.indexed)} />
                    <Pair label="Updated" value={String(source.replaced)} />
                    <Pair label="Deleted" value={String(source.deleted)} />
                    <Pair label="Unsupported" value={String(source.unsupported)} />
                    <Pair label="Failed" value={String(source.failed)} />
                  </dl>
                  <p className="mt-2 text-xs text-ink-500">
                    Last attempted {formatDateTime(source.lastAttemptedAt)}
                  </p>
                  {source.detail ? (
                    <p className="mt-1.5 text-xs text-amber-700">{source.detail}</p>
                  ) : null}
                </div>
              ))}

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
