import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type {
  ReportEnvelope,
  ReportHistory,
  ReportPeriod,
  ReportType,
} from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { REPORT_TYPES, describePeriod, describeProvenance } from "../../lib/digest";
import { useTwins } from "../../state/TwinContext";
import { MailboxPanel } from "../email/MailboxPanel";
import { Badge, Button, EmptyState, ErrorNotice, SectionHeading, Spinner } from "../ui";
import { WeeklyReportPanel } from "./WeeklyReportPanel";

/**
 * What happened?
 *
 * That is the only question this section answers, and separating it from "what
 * do I need to do" is the point of the change that produced this file. Reports
 * used to open on a task dashboard, so the two questions shared a screen and
 * neither was answered well. Tasks now has its own page; this one holds
 * documents.
 *
 * Two rules the earlier version got wrong:
 *
 * **A period with no report is not a report.** The history lists what was
 * actually generated. It used to offer every month back to the epoch, each
 * opening a blank document — which taught a person that reports are empty.
 *
 * **A report is a document, not a live view.** Once generated it does not
 * change: the server serves the snapshot, and the download is rendered from
 * that same snapshot rather than rebuilt, so what is on screen and what is in
 * the file are the same thing next month as today.
 */
export function ReportsPanel() {
  const { currentTwin } = useTwins();
  const userId = currentTwin?.id ?? null;

  const [reportType, setReportType] = useState<ReportType>("weekly_email_digest");
  const [periodKey, setPeriodKey] = useState<string | null>(null);
  const [history, setHistory] = useState<ReportHistory | null>(null);
  const [envelope, setEnvelope] = useState<ReportEnvelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changeType = useCallback((next: ReportType) => {
    setReportType(next);
    // A week key means nothing to a monthly report, and carrying it across
    // would ask for a month that does not exist.
    setPeriodKey(null);
    setEnvelope(null);
  }, []);

  const load = useCallback(async () => {
    if (!userId) {
      setHistory(null);
      setEnvelope(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    setEnvelope(null);
    setHistory(null);

    try {
      const [found, current] = await Promise.all([
        api.getReportHistory(userId, { reportType }),
        api.getReport(userId, { reportType, period: periodKey ?? undefined }),
      ]);

      setHistory(found);
      setEnvelope(current);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Unable to load this report.",
      );
    } finally {
      setLoading(false);
    }
  }, [userId, reportType, periodKey]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveDocument() {
    if (!userId || !envelope) return;

    setDownloading(true);
    setError(null);

    try {
      const { blob, filename } = await api.downloadReport(userId, {
        reportType: envelope.report_type,
        period: envelope.period.key,
      });

      // An object URL and a synthetic click: the alternative is a plain link,
      // which cannot carry the identity header, which would mean putting the
      // user id in a URL that lands in every access log.
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename ?? "report.docx";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The report could not be saved.",
      );
    } finally {
      setDownloading(false);
    }
  }

  if (!userId) {
    return (
      <EmptyState
        title="No Digital Twin selected"
        description="Reports are private to one person. Choose a Digital Twin in the sidebar to see theirs."
      />
    );
  }

  const periods: ReportPeriod[] = history?.available_periods ?? [];
  const selected = envelope?.period ?? periods[0] ?? null;
  const stored = history?.items ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <SectionHeading
        title="Reports"
        description="A short management summary of what happened, for a week or a month."
        actions={
          envelope && reportType !== "weekly_work" ? (
            <Button
              type="button"
              variant="primary"
              disabled={downloading}
              onClick={() => void saveDocument()}
            >
              {downloading ? "Preparing…" : "Download"}
            </Button>
          ) : null
        }
      />

      <div className="flex flex-wrap items-center gap-2 border-b border-ink-200 px-5 py-2.5">
        <div role="tablist" aria-label="Report" className="flex flex-wrap gap-1">
          {REPORT_TYPES.map((item) => (
            <button
              key={item.id}
              role="tab"
              aria-selected={item.id === reportType}
              title={item.hint}
              onClick={() => changeType(item.id)}
              className={`rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
                item.id === reportType
                  ? "bg-accent-50 text-accent-700"
                  : "text-ink-600 hover:bg-ink-100"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <label htmlFor="report-period" className="text-xs text-ink-500">
            Period
          </label>
          <select
            id="report-period"
            value={selected?.key ?? ""}
            onChange={(event) => setPeriodKey(event.target.value || null)}
            className="rounded-md border border-ink-200 px-2 py-1.5 text-sm"
          >
            {periods.map((period) => (
              <option key={period.key} value={period.key}>
                {describePeriod(period)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {error ? (
          <div className="mb-3">
            <ErrorNotice message={error} onRetry={() => void load()} />
          </div>
        ) : null}

        {reportType !== "weekly_work" ? (
          <div className="mb-4">
            <MailboxPanel onChanged={() => void load()} />
          </div>
        ) : null}

        {/* The work report keeps its own interactive screen: marking a meeting
            attended and preparing an escalation are actions on live work, and
            a stored snapshot has nothing to act on. */}
        {reportType === "weekly_work" ? (
          <WeeklyReportPanel />
        ) : loading ? (
          <div className="py-10 text-center">
            <Spinner label="Preparing the report" />
          </div>
        ) : envelope ? (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Badge tone="info">{envelope.period.label}</Badge>
              {envelope.is_provisional ? (
                <Badge tone="warning">In progress</Badge>
              ) : null}
              <span className="text-xs text-ink-500">
                {describeProvenance(envelope)} Generated{" "}
                {formatDateTime(envelope.generated_at)}.
              </span>
            </div>

            <ActivityDocument envelope={envelope} />
          </>
        ) : (
          <EmptyState
            title="No report yet"
            description="Your first report will appear here after your first reporting period."
          />
        )}

        {reportType === "weekly_work" ? null : (
          <StoredReports items={stored} onOpen={setPeriodKey} />
        )}
      </div>
    </div>
  );
}

/**
 * The report itself, read out of the stored snapshot.
 *
 * Rendered from `content.activity`, which the server assembled from real
 * counts and rows — no model wrote any of this, and no section here is padded
 * to a fixed shape. A period with no risks has no risks heading, because a
 * heading over the word "None" every week trains a reader to skim.
 */
function ActivityDocument({ envelope }: { envelope: ReportEnvelope }) {
  const content = (envelope.content ?? {}) as Record<string, unknown>;
  const activity = (content.activity ?? {}) as Record<string, unknown>;
  const sections = Array.isArray(activity.sections) ? activity.sections : [];

  if (sections.length === 0) {
    return (
      <EmptyState
        title="Nothing recorded for this period"
        description={
          envelope.detail ??
          "This report has no content, and nothing has been made up in its place."
        }
      />
    );
  }

  return (
    <article className="space-y-4">
      {sections.map((raw, index) => {
        const section = (raw ?? {}) as Record<string, unknown>;
        const title = typeof section.title === "string" ? section.title : "";
        const note = typeof section.note === "string" ? section.note : null;
        const lines = Array.isArray(section.lines) ? section.lines : [];

        return (
          <section key={`${title}-${index}`}>
            <h3 className="text-sm font-semibold text-ink-900">
              {index + 1}. {title}
            </h3>

            {note ? (
              <p className="mt-1 text-sm italic leading-relaxed text-ink-500">{note}</p>
            ) : null}

            {lines.length > 0 ? (
              <ul className="mt-1.5 space-y-1">
                {lines.map((entry, position) => {
                  const line = (entry ?? {}) as Record<string, unknown>;
                  const text = typeof line.text === "string" ? line.text : "";
                  const detail =
                    typeof line.detail === "string" ? line.detail : null;

                  return (
                    <li key={`${text}-${position}`} className="text-sm text-ink-700">
                      <span className="text-ink-400">— </span>
                      {text}
                      {detail ? (
                        <span className="text-ink-500"> · {detail}</span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </section>
        );
      })}
    </article>
  );
}

/**
 * The short history: what has actually been generated, newest first.
 *
 * Capped by the server at five. An unbounded list turns this page into an
 * archive index, and nobody opens the fourteenth week back.
 */
function StoredReports({
  items,
  onOpen,
}: {
  items: ReportHistory["items"];
  onOpen: (key: string) => void;
}) {
  if (items.length === 0) return null;

  return (
    <section className="mt-6 border-t border-ink-200 pt-3">
      <p className="text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
        Recent reports
      </p>
      <ul className="mt-1.5 divide-y divide-ink-100 rounded-md border border-ink-200">
        {items.map((item) => (
          <li
            key={`${item.report_type}-${item.period.key}`}
            className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-sm text-ink-800">{item.period.label}</p>
              <p className="text-xs text-ink-500">
                Generated {formatDateTime(item.generated_at)}
                {item.status === "unavailable" ? " · email unavailable" : null}
              </p>
            </div>
            <Button type="button" variant="ghost" onClick={() => onOpen(item.period.key)}>
              Open
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
