import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type {
  ActivityReport,
  ActivityReportPending,
  ReportEnvelope,
  ReportHistory,
  ReportPeriod,
  ReportType,
} from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { REPORT_TYPES, describePeriod, describeProvenance } from "../../lib/digest";
import { fallbackFor, faultMessage } from "../../lib/reportFault";
import { useTwins } from "../../state/TwinContext";
import { MailboxPanel } from "../email/MailboxPanel";
import { Badge, Button, EmptyState, ErrorNotice, SectionHeading, Spinner } from "../ui";
import { ActivityReportDocument } from "./ActivityReportDocument";
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
 *
 * That second rule now has an exception, and it is deliberate. The detailed
 * activity report is computed from correspondence on request rather than
 * stored, so the two live side by side: `GET /reports/activity` produces what
 * is on screen, and the stored snapshots remain listed and downloadable
 * underneath. Showing a cached digest where a person asked for the detailed
 * report would be the silent-stale-snapshot problem, and showing only the
 * detailed one would throw away history — so neither replaces the other.
 */
/**
 * The last day a period actually includes.
 *
 * `ReportPeriod.end` is exclusive — a month ends at midnight on the 1st of the
 * next one — while the backend's `end` query parameter is an inclusive date.
 * Passing `end` straight through would ask for a report one day longer than
 * the period selected, which for a month means reporting a day of the next
 * one.
 */
/**
 * How long the page waits for somebody else's generation, and how often it
 * asks. Five seconds apart, for four minutes — longer than a report takes when
 * it is working, short enough that a stuck one is reported rather than hidden.
 */
const POLL_INTERVAL_MS = 5_000;
const POLL_ATTEMPTS = 48;

function lastDayOf(period: ReportPeriod): string {
  const end = new Date(period.end);
  end.setUTCDate(end.getUTCDate() - 1);

  return end.toISOString().slice(0, 10);
}

export function ReportsPanel() {
  const { currentTwin } = useTwins();
  const userId = currentTwin?.id ?? null;

  const [reportType, setReportType] = useState<ReportType>("weekly_email_digest");
  const [periodKey, setPeriodKey] = useState<string | null>(null);
  const [history, setHistory] = useState<ReportHistory | null>(null);
  const [envelope, setEnvelope] = useState<ReportEnvelope | null>(null);
  const [activity, setActivity] = useState<ActivityReport | null>(null);
  const [pending, setPending] = useState<ActivityReportPending | null>(null);
  const [activityFault, setActivityFault] = useState<Error | null>(null);
  const [generating, setGenerating] = useState(false);
  const [attempts, setAttempts] = useState(0);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // What has already been asked for, and which request is the current one.
  //
  // Generating the detailed report is minutes of server work — a mailbox read
  // and a model call per conversation — so a duplicate is not a wasted
  // round trip, it is a second pipeline. Two things used to cause them. The
  // effect below was keyed on the period *object*, and every reload produces
  // an equal period with a fresh identity, so re-reading the stored report
  // re-ran the analysis. And nothing tracked what was in flight, so changing
  // the period while one was running left both running and let whichever
  // finished last win.
  const requested = useRef<string | null>(null);
  const runId = useRef(0);

  const changeType = useCallback((next: ReportType) => {
    setReportType(next);
    requested.current = null;
    // A week key means nothing to a monthly report, and carrying it across
    // would ask for a month that does not exist.
    setPeriodKey(null);
    setEnvelope(null);
    setActivity(null);
    setPending(null);
    setActivityFault(null);
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
    setActivity(null);
    setPending(null);
    setActivityFault(null);

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

  /**
   * Ask for this period's report.
   *
   * `rebuild` is the Regenerate button and nothing else. A plain `GET` reuses
   * what the backend stored — a period that has closed comes back in
   * milliseconds — so opening the tab, switching back to a period and
   * reloading the page all cost a database read rather than a pipeline run.
   */
  const generate = useCallback(
    async (period: ReportPeriod | null, { rebuild = false } = {}) => {
      if (!userId || !period) return;

      const id = (runId.current += 1);
      const superseded = () => runId.current !== id;

      setGenerating(true);
      setActivityFault(null);

      const window = {
        start: period.start.slice(0, 10),
        // A period still running has no last day yet. Omitting `end` is what
        // makes the backend run the window up to the end of today and label
        // the result provisional, rather than claiming a period that has not
        // happened.
        end: period.is_complete ? lastDayOf(period) : undefined,
      };

      try {
        const answer = rebuild
          ? await api.regenerateActivityReport(userId, window)
          : await api.getActivityReport(userId, window);

        if (superseded()) return;

        if (answer.state === "generating") {
          // Somebody else — another tab, another person — is already building
          // this one. Wait for theirs rather than starting a second.
          setPending(answer);
          setActivity(null);
        } else {
          setPending(null);
          setActivity(answer);
        }
      } catch (cause) {
        if (superseded()) return;

        // The failure itself is kept, not a string made from it. Whether the
        // stored digest may stand in for the detailed report depends on *why*
        // this failed, and a message cannot be asked that.
        setActivity(null);
        setPending(null);
        setActivityFault(
          cause instanceof Error
            ? cause
            : new Error("The detailed report could not be generated."),
        );
      } finally {
        if (!superseded()) setGenerating(false);
      }
    },
    [userId],
  );

  /**
   * While another request is building this report, ask again — a bounded
   * number of times.
   *
   * Bounded because an unbounded poll behind a spinner is how a page ends up
   * claiming to be working long after nothing is. When the attempts run out
   * the panel says so and offers the button, rather than spinning for ever.
   */
  useEffect(() => {
    if (!pending || !envelope || attempts >= POLL_ATTEMPTS) return;

    const timer = window.setTimeout(() => {
      setAttempts((count) => count + 1);
      void generate(envelope.period);
    }, POLL_INTERVAL_MS);

    return () => window.clearTimeout(timer);
  }, [pending, attempts, envelope, generate]);

  // Generated when the tab or the *period* changes — the period's key, not the
  // object carrying it, because an equal period arriving from a fresh fetch is
  // not a new question and must not start a second analysis.
  const periodKeyInView = envelope?.period.key ?? null;

  useEffect(() => {
    if (reportType === "weekly_work" || !userId) return;

    const period = envelope?.period ?? null;

    if (!period) return;

    const asked = `${userId}:${reportType}:${period.key}`;

    if (requested.current === asked) return;

    requested.current = asked;
    setAttempts(0);
    void generate(period);
    // `envelope` is deliberately absent: its identity changes on every load,
    // and the key above is what decides whether there is a new question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, reportType, periodKeyInView, generate]);

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
            <div className="flex items-center gap-2">
              {/* The detailed report is computed per request, so regenerating
                  is a real action rather than a cache bust. */}
              <Button
                type="button"
                variant="secondary"
                disabled={generating}
                onClick={() => {
                  // The only path that rebuilds a report. Everything else on
                  // this screen reads what is stored.
                  requested.current = `${userId}:${reportType}:${envelope.period.key}`;
                  setAttempts(0);
                  void generate(envelope.period, { rebuild: true });
                }}
              >
                {generating ? "Analysing…" : "Regenerate"}
              </Button>
              <Button
                type="button"
                variant="primary"
                disabled={downloading}
                onClick={() => void saveDocument()}
              >
                {downloading ? "Preparing…" : "Download"}
              </Button>
            </div>
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
            {activityFault && fallbackFor(activityFault) === "error" ? (
              <div className="mb-3">
                <ErrorNotice
                  message={faultMessage(activityFault)}
                  onRetry={() => void generate(envelope.period)}
                />
              </div>
            ) : null}

            {generating && !pending ? (
              <div className="py-10 text-center">
                <Spinner
                  label={
                    activity
                      ? "Rebuilding this report"
                      : "Reading this period's report"
                  }
                />
              </div>
            ) : pending ? (
              <GeneratingNotice
                pending={pending}
                exhausted={attempts >= POLL_ATTEMPTS}
                onCheck={() => {
                  setAttempts(0);
                  void generate(envelope.period);
                }}
              />
            ) : activity ? (
              <ActivityReportDocument report={activity} />
            ) : fallbackFor(activityFault) === "error" ? (
              /* Deliberately nothing. A server fault is not a quiet month, and
                 the stored digest rendered underneath one reads as the answer:
                 that is how an HTTP 500 sat on this screen behind "83 messages
                 received", looking like a report. The error above is the whole
                 content until the request succeeds. */
              null
            ) : (
              /* No mailbox connected — a configuration state, not a failure —
                 so the stored digest is shown instead of nothing, with its own
                 provenance line saying what it is. */
              <>
                {activityFault ? (
                  <p className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    {faultMessage(activityFault)} The stored summary below is
                    shown in its place.
                  </p>
                ) : null}

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
            )}
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
 * Somebody is already building this report, and this says so out loud.
 *
 * Deliberately not a spinner. A spinner with no end is how a page that is
 * waiting becomes indistinguishable from a page that is broken; this names
 * what is happening, how long it has been happening, and what the reader can
 * do when it has been too long.
 */
function GeneratingNotice({
  pending,
  exhausted,
  onCheck,
}: {
  pending: ActivityReportPending;
  exhausted: boolean;
  onCheck: () => void;
}) {
  const minutes = Math.floor(pending.elapsed_seconds / 60);
  const elapsed =
    minutes >= 1
      ? `${minutes} minute${minutes === 1 ? "" : "s"}`
      : `${Math.round(pending.elapsed_seconds)} seconds`;

  return (
    <div className="rounded-lg border border-ink-200 bg-ink-50 px-4 py-5 text-center">
      <div className="flex items-center justify-center gap-2">
        {exhausted ? null : <Spinner label="" />}
        <p className="text-sm font-medium text-ink-800">
          This report is being generated
        </p>
      </div>

      <p className="mx-auto mt-2 max-w-md text-xs text-ink-600">
        Started {elapsed} ago, in this session or another one. It is stored when
        it finishes, and every request after that reads it from storage rather
        than rebuilding it.
      </p>

      {exhausted ? (
        <div className="mt-3">
          <p className="text-xs text-amber-700">
            It has been running longer than a report usually takes. Nothing has
            been lost — checking again is safe.
          </p>
          <Button type="button" variant="secondary" onClick={onCheck}>
            Check again
          </Button>
        </div>
      ) : null}
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
                  const detail = typeof line.detail === "string" ? line.detail : null;

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
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpen(item.period.key)}
            >
              Open
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
