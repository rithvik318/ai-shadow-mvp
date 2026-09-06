import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type {
  CalendarEvent,
  Escalation,
  EventStatus,
  Task,
  WeeklyReport,
} from "../../api/types";
import { formatDateTime } from "../../lib/format";
import {
  describeEvent,
  describeTask,
  hasEscalationTarget,
  isEmpty,
  openTasks,
} from "../../lib/report";
import { useTwins } from "../../state/TwinContext";
import { Badge, Button, EmptyState, ErrorNotice, SectionHeading, Spinner } from "../ui";

/**
 * One person's week: what is open, what is late, what needs somebody else, and
 * which meetings nobody has answered for.
 *
 * Every judgement on this screen was made by the backend. Urgency, overdue and
 * escalation all arrive decided; this component maps them to colours and words
 * through `lib/report.ts` and does no arithmetic on a date. That is what keeps
 * one threshold in one place — change `TASK_WARNING_DAYS` on the server and
 * this screen moves with it.
 *
 * Nothing here sends anything. "Prepare escalation" and "Draft follow-up" hand
 * the composer a starting point; a message still leaves only after the
 * approve-then-send path in the Email Agent.
 */
export function WeeklyReportPanel() {
  const { currentTwin } = useTwins();
  const userId = currentTwin?.id ?? null;

  const [report, setReport] = useState<WeeklyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!userId) {
      setReport(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      setReport(await api.getWeeklyReport(userId));
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Unable to load the weekly report.",
      );
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(key: string, work: () => Promise<unknown>) {
    setBusy(key);
    setError(null);

    try {
      await work();
      // Re-read rather than patching in place: completing a task can change
      // its urgency, remove an escalation and move a count, and reproducing
      // those rules here would be the second copy this design avoids.
      await load();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "That change could not be saved.",
      );
    } finally {
      setBusy(null);
    }
  }

  if (!userId) {
    return (
      <section className="flex h-full flex-col bg-white">
        <Header />
        <div className="flex-1 px-6 py-5">
          <EmptyState
            title="No Digital Twin selected"
            description="Choose a Digital Twin in the sidebar to see their week."
          />
        </div>
      </section>
    );
  }

  return (
    <section className="flex h-full min-w-0 flex-col bg-white">
      <Header
        actions={
          <Button type="button" onClick={() => void load()} disabled={loading}>
            Refresh
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {loading ? (
          <div className="py-10 text-center">
            <Spinner label="Loading the weekly report" />
          </div>
        ) : error && !report ? (
          <ErrorNotice message={error} onRetry={() => void load()} />
        ) : !report ? null : (
          <div className="space-y-7">
            {error ? <ErrorNotice message={error} onRetry={() => void load()} /> : null}

            <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
              <Stat
                label="Overdue"
                value={report.summary.overdue_count}
                tone="danger"
              />
              <Stat
                label="Due soon"
                value={report.summary.due_soon_count}
                tone="warning"
              />
              <Stat
                label="Escalations"
                value={report.summary.escalation_count}
                tone="danger"
              />
              <Stat
                label="Completed"
                value={report.summary.completed_count}
                tone="positive"
              />
              <Stat
                label="Meetings to confirm"
                value={report.summary.events_needing_answer_count}
                tone="neutral"
              />
            </div>

            <p className="text-xs text-ink-500">
              {formatDateTime(report.period_start)} —{" "}
              {formatDateTime(report.period_end)}
            </p>

            {isEmpty(report) ? (
              <EmptyState
                title="Nothing to report this week"
                description="No open tasks, no meetings and nothing needing escalation. Tasks created from email follow-ups appear here automatically."
              />
            ) : null}

            {report.escalations.length > 0 ? (
              <Escalations
                items={report.escalations}
                busy={busy}
                onComplete={(task) =>
                  act(`complete:${task.id}`, () => api.completeTask(userId, task.id))
                }
              />
            ) : null}

            <Tasks
              report={report}
              busy={busy}
              onComplete={(task) =>
                act(`complete:${task.id}`, () => api.completeTask(userId, task.id))
              }
              onStart={(task) =>
                act(`start:${task.id}`, () =>
                  api.updateTask(userId, task.id, { status: "in_progress" }),
                )
              }
            />

            <Events
              report={report}
              busy={busy}
              onMark={(event, status) =>
                act(`event:${event.id}`, () =>
                  api.markAttendance(userId, event.id, status),
                )
              }
            />

            <FollowUps items={report.follow_ups} />
          </div>
        )}
      </div>
    </section>
  );
}

function Header({ actions }: { actions?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-ink-200 px-6 py-4">
      <div>
        <h1 className="text-base font-semibold text-ink-900">Weekly Report</h1>
        <p className="mt-0.5 text-xs text-ink-500">
          Open work, meetings and anything needing somebody else. Nothing here is sent
          automatically.
        </p>
      </div>
      {actions}
    </header>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "positive" | "neutral" | "warning" | "danger";
}) {
  // A zero is deliberately not coloured: "0 overdue" in red reads as an alarm.
  const colour =
    value === 0
      ? "text-ink-900"
      : tone === "danger"
        ? "text-rose-700"
        : tone === "warning"
          ? "text-amber-700"
          : tone === "positive"
            ? "text-emerald-700"
            : "text-ink-900";

  return (
    <div className="rounded-lg border border-ink-200 px-4 py-3">
      <p className={`text-xl font-semibold tabular-nums ${colour}`}>{value}</p>
      <p className="mt-0.5 text-xs text-ink-500">{label}</p>
    </div>
  );
}

function Escalations({
  items,
  busy,
  onComplete,
}: {
  items: Escalation[];
  busy: string | null;
  onComplete: (task: Task) => void;
}) {
  return (
    <div>
      <SectionHeading
        title="Escalation required"
        description="Open work that is no longer only its owner's to finish."
      />
      <ul className="mt-3 space-y-2.5">
        {items.map((item) => {
          const targeted = hasEscalationTarget(item);

          return (
            <li
              key={item.task.id}
              className="rounded-lg border border-rose-200 bg-rose-50/50 px-4 py-3"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="font-medium text-ink-900">{item.task.title}</p>
                <Badge tone="danger">Escalation required</Badge>
              </div>

              <dl className="mt-2 space-y-1 text-sm text-ink-700">
                <Row label="Reason" value={item.reason} />
                <Row label="Suggested action" value={item.recommended_action} />
                <Row
                  label="Target"
                  value={item.escalation_contact_display}
                  // Not an error, and not styled as one: nobody being
                  // configured is a setup step, and a red field would send
                  // somebody looking for a fault.
                  muted={!targeted}
                />
                {item.contact_address ? (
                  <Row label="Concerns" value={item.contact_address} />
                ) : null}
              </dl>

              <div className="mt-2.5 flex flex-wrap gap-2">
                <Button
                  type="button"
                  disabled={!targeted}
                  title={
                    targeted
                      ? undefined
                      : "Set ESCALATION_CONTACT_ADDRESS on the server to enable this."
                  }
                  onClick={() => undefined}
                >
                  Prepare escalation email
                </Button>
                <Button
                  type="button"
                  onClick={() => onComplete(item.task)}
                  disabled={busy === `complete:${item.task.id}`}
                >
                  {busy === `complete:${item.task.id}` ? "Saving…" : "Mark resolved"}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Row({
  label,
  value,
  muted = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-x-2">
      <dt className="text-ink-500">{label}:</dt>
      <dd className={muted ? "text-ink-500 italic" : "text-ink-800"}>{value}</dd>
    </div>
  );
}

function Tasks({
  report,
  busy,
  onComplete,
  onStart,
}: {
  report: WeeklyReport;
  busy: string | null;
  onComplete: (task: Task) => void;
  onStart: (task: Task) => void;
}) {
  const open = openTasks(report);

  return (
    <div>
      <SectionHeading
        title="Tasks"
        description="Most urgent first. Colour is the server's verdict, not a date read here."
      />

      {open.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-ink-300 px-4 py-6 text-center text-sm text-ink-500">
          No open tasks.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {open.map((line) => (
            <li
              key={line.task.id}
              className={`rounded-lg border px-4 py-3 ${
                line.signal === "red"
                  ? "border-rose-200 bg-rose-50/40"
                  : line.signal === "yellow"
                    ? "border-amber-200 bg-amber-50/40"
                    : "border-ink-200"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium text-ink-900">{line.task.title}</p>
                  <p className="mt-0.5 text-xs text-ink-600">{line.deadline}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={line.tone}>{line.statusLabel}</Badge>
                </div>
              </div>

              <div className="mt-2.5 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="primary"
                  onClick={() => onComplete(line.task)}
                  disabled={busy === `complete:${line.task.id}`}
                  aria-label={`Mark ${line.task.title} complete`}
                >
                  {busy === `complete:${line.task.id}` ? "Saving…" : "Mark complete"}
                </Button>
                {line.task.status === "todo" ? (
                  <Button
                    type="button"
                    onClick={() => onStart(line.task)}
                    disabled={busy === `start:${line.task.id}`}
                  >
                    {busy === `start:${line.task.id}` ? "Saving…" : "Start"}
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      {report.completed.length > 0 ? (
        <p className="mt-3 text-xs text-emerald-700">
          {report.completed.length} completed this week.
        </p>
      ) : null}
    </div>
  );
}

const ANSWERS: Array<{ status: EventStatus; label: string }> = [
  { status: "attended", label: "Attended" },
  { status: "missed", label: "Missed" },
  { status: "unknown", label: "Not sure" },
];

function Events({
  report,
  busy,
  onMark,
}: {
  report: WeeklyReport;
  busy: string | null;
  onMark: (event: CalendarEvent, status: EventStatus) => void;
}) {
  return (
    <div>
      <SectionHeading
        title="Meetings"
        description="Attendance is only ever what somebody recorded — never inferred from a meeting existing."
      />

      {/* Said plainly rather than implied by an empty list: "no meetings" and
          "no calendar connected" are different facts. */}
      <p className="mt-2 text-xs text-ink-500">{report.calendar_detail}</p>

      {report.events.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-ink-300 px-4 py-6 text-center text-sm text-ink-500">
          No meetings recorded for this week.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {report.events.map((event) => {
            const line = describeEvent(event);

            return (
              <li key={event.id} className="rounded-lg border border-ink-200 px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-medium text-ink-900">{event.title}</p>
                    <p className="mt-0.5 text-xs text-ink-500">
                      {formatDateTime(event.starts_at)}
                      {event.location ? ` · ${event.location}` : ""}
                    </p>
                  </div>
                  <Badge tone={line.tone}>{line.statusLabel}</Badge>
                </div>

                {line.needsAnswer ? (
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <span className="text-xs text-ink-600">Did you attend?</span>
                    {ANSWERS.map((answer) => (
                      <Button
                        key={answer.status}
                        type="button"
                        onClick={() => onMark(event, answer.status)}
                        disabled={busy === `event:${event.id}`}
                      >
                        {answer.label}
                      </Button>
                    ))}
                  </div>
                ) : event.attendance_evidence !== "none" ? (
                  <p className="mt-1.5 text-xs text-ink-500">
                    Recorded {formatDateTime(event.attendance_recorded_at)}
                    {event.attendance_evidence === "user" ? " by you" : ""}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function FollowUps({ items }: { items: Task[] }) {
  if (items.length === 0) return null;

  return (
    <div>
      <SectionHeading
        title="Email follow-ups"
        description="Open tasks that came from a message rather than being typed in."
      />
      <ul className="mt-3 space-y-2">
        {items.map((task) => {
          const line = describeTask(task);

          return (
            <li
              key={task.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ink-200 px-4 py-2.5"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-ink-900">{task.title}</p>
                {/* The address, never the display string — the same split the
                    backend keeps, carried through to what is shown. */}
                {task.contact_address ? (
                  <p className="text-xs text-ink-500">{task.contact_address}</p>
                ) : null}
              </div>
              <Badge tone={line.tone}>{line.deadline}</Badge>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
