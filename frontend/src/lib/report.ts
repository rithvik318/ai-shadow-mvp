/**
 * The weekly report's presentation logic, kept out of React so it is testable.
 *
 * The dividing line matters. **Nothing here decides whether a task is urgent,
 * overdue, or in need of escalation.** The backend decides all three and sends
 * `urgency`, `display_status` and `escalation_required`; recomputing any of
 * them from a date here would be a second copy of a threshold, and the first
 * time somebody edited one copy the two would disagree.
 *
 * What lives here is everything below that: which colour a severity maps to,
 * what a status is called in English, how a deadline reads as a phrase, and
 * how the report's sections are assembled into one ordered list. Those are
 * presentation decisions, they are pure, and a component that made them inline
 * could not be tested without a browser.
 */

import type {
  CalendarEvent,
  Escalation,
  EventStatus,
  Task,
  TaskDisplayStatus,
  TaskUrgency,
  WeeklyReport,
} from "../api/types";

export type Tone = "positive" | "neutral" | "warning" | "danger" | "info";

/** The three-colour scheme the report is read by. */
export type Signal = "green" | "yellow" | "red";

/**
 * Severity to colour. The whole mapping, in one place.
 *
 * `normal` is green rather than grey: the report's job is to make "nothing to
 * worry about" as readable as "act now", and a neutral badge beside an amber
 * one reads as absence of information rather than as reassurance.
 */
const SIGNAL_BY_URGENCY: Record<TaskUrgency, Signal> = {
  normal: "green",
  warning: "yellow",
  critical: "red",
};

const TONE_BY_SIGNAL: Record<Signal, Tone> = {
  green: "positive",
  yellow: "warning",
  red: "danger",
};

export function signalFor(urgency: TaskUrgency): Signal {
  return SIGNAL_BY_URGENCY[urgency];
}

export function toneFor(urgency: TaskUrgency): Tone {
  return TONE_BY_SIGNAL[SIGNAL_BY_URGENCY[urgency]];
}

const TASK_STATUS_LABELS: Record<TaskDisplayStatus, string> = {
  todo: "To do",
  in_progress: "In progress",
  completed: "Completed",
  blocked: "Blocked",
  cancelled: "Cancelled",
  overdue: "Overdue",
  escalation_required: "Escalation required",
};

export function describeTaskStatus(status: TaskDisplayStatus): string {
  return TASK_STATUS_LABELS[status];
}

const DAY_MS = 86_400_000;

/**
 * How a deadline reads, as a phrase rather than a date.
 *
 * Rounded *away* from now in both directions, so a task 1.2 days late reads
 * "Overdue by 2 days" rather than "by 1". Under-reporting lateness is the
 * error that matters here: a report that makes something sound less late than
 * it is defeats its own purpose.
 */
export function describeDeadline(
  dueAt: string | null,
  now: number = Date.now(),
): string {
  if (!dueAt) return "No deadline";

  const due = Date.parse(dueAt);
  if (Number.isNaN(due)) return "No deadline";

  const deltaDays = (due - now) / DAY_MS;

  if (deltaDays < 0) {
    const late = Math.max(1, Math.ceil(Math.abs(deltaDays)));
    return `Overdue by ${late} day${late === 1 ? "" : "s"}`;
  }

  if (deltaDays < 1) return "Due today";

  const remaining = Math.ceil(deltaDays);
  return `Due in ${remaining} day${remaining === 1 ? "" : "s"}`;
}

export interface TaskLine {
  task: Task;
  signal: Signal;
  tone: Tone;
  statusLabel: string;
  deadline: string;
  /** The one-line form the brief is read as: `[RED] Approval — Overdue by 4 days`. */
  summary: string;
}

export function describeTask(task: Task, now: number = Date.now()): TaskLine {
  const signal = signalFor(task.urgency);
  const statusLabel = describeTaskStatus(task.display_status);

  // A finished task's deadline is history and saying "overdue by 4 days"
  // beside "Completed" reads as a contradiction.
  const deadline =
    task.status === "completed" || task.status === "cancelled"
      ? statusLabel
      : describeDeadline(task.due_at, now);

  return {
    task,
    signal,
    tone: TONE_BY_SIGNAL[signal],
    statusLabel,
    deadline,
    summary: `[${signal.toUpperCase()}] ${task.title} — ${deadline}`,
  };
}

const EVENT_STATUS_LABELS: Record<EventStatus, string> = {
  scheduled: "Scheduled",
  attended: "Attended",
  missed: "Missed",
  cancelled: "Cancelled",
  unknown: "Not recorded",
};

const EVENT_TONES: Record<EventStatus, Tone> = {
  scheduled: "info",
  attended: "positive",
  missed: "danger",
  cancelled: "neutral",
  // Neutral, not amber. "Nobody has said" is a question, not a problem, and
  // colouring it as a warning would imply the meeting was missed.
  unknown: "neutral",
};

export interface EventLine {
  event: CalendarEvent;
  statusLabel: string;
  tone: Tone;
  needsAnswer: boolean;
}

export function describeEvent(event: CalendarEvent): EventLine {
  return {
    event,
    statusLabel: EVENT_STATUS_LABELS[event.display_status],
    tone: EVENT_TONES[event.display_status],
    needsAnswer: event.needs_answer,
  };
}

/** Whether an escalation has somewhere to go, or only a statement that it
 * does not. Drives whether the UI offers "Prepare escalation email". */
export function hasEscalationTarget(escalation: Escalation): boolean {
  return (
    escalation.target_source === "configured" &&
    Boolean(escalation.escalation_contact_address)
  );
}

/**
 * Every open task in the report, once, most urgent first.
 *
 * The report's sections overlap by design — an overdue high-priority
 * follow-up is in three of them — so a flat list has to deduplicate by id or
 * the same task appears three times. Sorted by severity and then by deadline,
 * because that is the order somebody works through them in.
 */
export function openTasks(report: WeeklyReport, now: number = Date.now()): TaskLine[] {
  const seen = new Map<string, Task>();

  for (const task of [...report.overdue, ...report.blocked, ...report.upcoming]) {
    if (!seen.has(task.id)) seen.set(task.id, task);
  }

  const rank: Record<TaskUrgency, number> = { critical: 0, warning: 1, normal: 2 };

  return [...seen.values()]
    .sort((left, right) => {
      const bySeverity = rank[left.urgency] - rank[right.urgency];
      if (bySeverity !== 0) return bySeverity;

      // Undated last: a task with no deadline is not more urgent than one with
      // a deadline today, and `Date.parse(null)` would sort it first.
      const leftDue = left.due_at ? Date.parse(left.due_at) : Number.POSITIVE_INFINITY;
      const rightDue = right.due_at
        ? Date.parse(right.due_at)
        : Number.POSITIVE_INFINITY;

      return leftDue - rightDue;
    })
    .map((task) => describeTask(task, now));
}

/** True when there is genuinely nothing to show — as distinct from a report
 * that failed to load, which the panel renders differently. */
export function isEmpty(report: WeeklyReport): boolean {
  return (
    report.upcoming.length === 0 &&
    report.overdue.length === 0 &&
    report.blocked.length === 0 &&
    report.completed.length === 0 &&
    report.escalations.length === 0 &&
    report.events.length === 0
  );
}
