/**
 * Reading the Tasks page: four colours, four groups, and what each row says.
 *
 * The colour is decided by the server — `task.signal` — and this module maps
 * it to the theme's tones and to words. It computes no dates: the two-day
 * boundary lives in `services/features/tasks/signal.py` and exists once, so
 * moving it moves the whole product rather than half of it.
 *
 * The lifecycle underneath has five statuses, an urgency scale and an
 * escalation verdict. None of that appears here. A person scanning their list
 * wants to know what is done, what is fine, what is slipping and what is late,
 * and adding a fifth idea to that would make the page harder to read without
 * making anybody's work clearer.
 */

import type { Task, TaskSignal } from "../api/types";

export type Tone = "positive" | "neutral" | "warning" | "danger";

export interface SignalStyle {
  /** The heading a group of these tasks sits under. */
  group: string;
  /** The word on the badge. */
  label: string;
  tone: Tone;
  /** One line explaining what the colour means, for the legend. */
  legend: string;
}

/**
 * The whole colour system, in one object.
 *
 * Ordered as the page reads: what needs doing now first, finished work last.
 * A page that opened with everything already done would bury the point.
 */
export const SIGNALS: Array<{ id: TaskSignal } & SignalStyle> = [
  {
    id: "urgent",
    group: "Urgent",
    label: "Urgent",
    tone: "danger",
    legend: "More than two days overdue",
  },
  {
    id: "attention",
    group: "Attention",
    label: "Attention",
    tone: "warning",
    legend: "Two days overdue",
  },
  {
    id: "todo",
    group: "To do",
    label: "To do",
    tone: "neutral",
    legend: "Not yet due, or only just past",
  },
  {
    id: "done",
    group: "Done",
    label: "Done",
    tone: "positive",
    legend: "Finished",
  },
];

export function styleFor(signal: TaskSignal): SignalStyle {
  return SIGNALS.find((entry) => entry.id === signal) ?? SIGNALS[2];
}

/**
 * The badge on one row.
 *
 * `done` is split by status rather than by signal: completed and cancelled
 * share a colour because neither needs attention, and they are still different
 * things. Saying "Done" over abandoned work would be the report claiming an
 * achievement nobody made.
 */
export function badgeFor(task: Task): { label: string; tone: Tone } {
  const style = styleFor(task.signal);

  if (task.signal === "done") {
    return {
      label: task.status === "cancelled" ? "Cancelled" : "Done",
      tone: style.tone,
    };
  }

  return { label: style.label, tone: style.tone };
}

/**
 * The line under a task's title: when it is due, or how late it is.
 *
 * Reads the server's `days_overdue` rather than subtracting dates here. The
 * two would disagree the first time one of them was changed, and the one that
 * matters is the one the colour was decided from.
 */
export function describeDeadline(task: Task, formatDate: (iso: string) => string): string {
  if (task.status === "completed" && task.completed_at) {
    return `Completed ${formatDate(task.completed_at)}`;
  }

  if (task.status === "cancelled") return "Cancelled";

  if (!task.due_at) return "No deadline";

  const late = task.days_overdue ?? 0;

  if (late > 0) return `${late} day${late === 1 ? "" : "s"} overdue`;

  return `Due ${formatDate(task.due_at)}`;
}

export interface TaskGroup {
  signal: TaskSignal;
  title: string;
  tasks: Task[];
}

/**
 * The list, grouped and ordered.
 *
 * Empty groups are dropped rather than shown as headings over nothing: a page
 * with four headings and one task under the last of them reads as three
 * failures. Within a group the most overdue comes first, then the soonest due,
 * then everything undated — which is the order somebody would work down.
 */
export function group(tasks: Task[]): TaskGroup[] {
  return SIGNALS.map((signal) => ({
    signal: signal.id,
    title: signal.group,
    tasks: tasks
      .filter((task) => task.signal === signal.id)
      .sort(compare),
  })).filter((entry) => entry.tasks.length > 0);
}

function compare(left: Task, right: Task): number {
  const lateness = (right.days_overdue ?? 0) - (left.days_overdue ?? 0);

  if (lateness !== 0) return lateness;

  if (left.due_at && right.due_at) return left.due_at.localeCompare(right.due_at);
  if (left.due_at) return -1;
  if (right.due_at) return 1;

  return left.title.localeCompare(right.title);
}

/** The counts across the top. Every signal is present, including the zeroes:
 * this strip is a summary, and a summary that hides its zeroes makes somebody
 * wonder whether the number is zero or missing. */
export function counts(tasks: Task[]): Array<{ signal: TaskSignal; label: string; count: number }> {
  return SIGNALS.map((signal) => ({
    signal: signal.id,
    label: signal.group,
    count: tasks.filter((task) => task.signal === signal.id).length,
  }));
}

/** Whether this task can still be marked done. Cancelled work cannot. */
export function isOpen(task: Task): boolean {
  return task.status !== "completed" && task.status !== "cancelled";
}
