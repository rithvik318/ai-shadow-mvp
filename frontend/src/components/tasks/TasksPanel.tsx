import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { Task } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import {
  SIGNALS,
  badgeFor,
  counts,
  describeDeadline,
  group,
  isOpen,
} from "../../lib/tasks";
import { useTwins } from "../../state/TwinContext";
import { Badge, Button, EmptyState, ErrorNotice, SectionHeading, Spinner } from "../ui";
import { NewTaskForm } from "./NewTaskForm";

/**
 * What do I need to do?
 *
 * That is the whole question this page answers, and the reason it is a page
 * rather than a section of the weekly report. Tasks were only reachable
 * through a report about the week, which put "what do I need to do" behind
 * "what happened" — the wrong way round for the thing somebody opens every
 * morning.
 *
 * Everything visible here is decided by the server. The colour comes from
 * `task.signal`, the lateness from `days_overdue`; this component subtracts no
 * dates and knows no thresholds. That is what keeps the two-day rule in one
 * place instead of two that disagree the first time either is edited.
 *
 * A task arrives one of two ways — a person types it, or a triaged email
 * becomes one — and both produce the same row with the same lifecycle. There
 * is deliberately no second kind of task and no second list.
 */
export function TasksPanel() {
  const { currentTwin } = useTwins();
  const userId = currentTwin?.id ?? null;

  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);

  const load = useCallback(async () => {
    if (!userId) {
      setTasks([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    // Cleared before the request: leaving the previous person's list up while
    // this one loads shows somebody else's work under this person's name.
    setTasks([]);

    try {
      const list = await api.listTasks(userId);
      setTasks(list.items);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Your tasks could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function markDone(task: Task) {
    if (!userId) return;

    setBusy(task.id);
    setError(null);

    try {
      await api.completeTask(userId, task.id);
      // Re-read rather than patching in place: completing a task changes its
      // colour, its group and three counts, and reproducing those rules here
      // would be the second copy this design avoids.
      await load();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "That task was not completed.",
      );
    } finally {
      setBusy(null);
    }
  }

  if (!userId) {
    return (
      <EmptyState
        title="No Digital Twin selected"
        description="Tasks are private to one person. Choose a Digital Twin in the sidebar to see theirs."
      />
    );
  }

  const groups = group(tasks);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <SectionHeading
        title="Tasks"
        description="Everything you still have to do, however it got here."
        actions={
          <Button
            type="button"
            variant="primary"
            onClick={() => setComposing((open) => !open)}
          >
            {composing ? "Cancel" : "+ New task"}
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {composing ? (
          <div className="mb-4">
            <NewTaskForm
              userId={userId}
              onCreated={() => {
                setComposing(false);
                void load();
              }}
              onCancel={() => setComposing(false)}
            />
          </div>
        ) : null}

        {error ? (
          <div className="mb-3">
            <ErrorNotice message={error} onRetry={() => void load()} />
          </div>
        ) : null}

        {loading ? (
          <div className="py-10 text-center">
            <Spinner label="Loading your tasks" />
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            title="No tasks yet"
            description="Tasks appear here when you add one, or when you add a triaged email from the Email Agent's follow-ups."
            action={
              <Button
                type="button"
                variant="primary"
                onClick={() => setComposing(true)}
              >
                + New task
              </Button>
            }
          />
        ) : (
          <>
            <Summary tasks={tasks} />

            <div className="mt-4 space-y-5">
              {groups.map((entry) => (
                <section key={entry.signal}>
                  <h3 className="mb-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
                    {entry.title}
                  </h3>
                  <ul className="space-y-2">
                    {entry.tasks.map((task) => (
                      <TaskRow
                        key={task.id}
                        task={task}
                        busy={busy === task.id}
                        onDone={() => void markDone(task)}
                      />
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </>
        )}

        <Legend />
      </div>
    </div>
  );
}

function Summary({ tasks }: { tasks: Task[] }) {
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {counts(tasks).map((entry) => (
        <div key={entry.signal} className="rounded-md border border-ink-200 px-3 py-2">
          <dt className="text-xs text-ink-500">{entry.label}</dt>
          <dd className="text-lg font-semibold text-ink-900">{entry.count}</dd>
        </div>
      ))}
    </dl>
  );
}

function TaskRow({
  task,
  busy,
  onDone,
}: {
  task: Task;
  busy: boolean;
  onDone: () => void;
}) {
  const badge = badgeFor(task);

  return (
    <li className="rounded-md border border-ink-200 bg-white px-3.5 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink-900">{task.title}</p>
          <p className="mt-0.5 text-sm text-ink-600">
            {describeDeadline(task, (iso) => formatDateTime(iso))}
          </p>
          {task.description ? (
            <p className="mt-1 text-sm leading-relaxed text-ink-600">
              {task.description}
            </p>
          ) : null}
          {task.source !== "manual" ? (
            <p className="mt-1 text-xs text-ink-500">
              From email{task.contact_display ? ` · ${task.contact_display}` : null}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Badge tone={badge.tone}>{badge.label}</Badge>
          {isOpen(task) ? (
            <Button type="button" onClick={onDone} disabled={busy}>
              {busy ? "Saving…" : "Mark done"}
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

/** Small on purpose. It explains the colours; it is not the point of the page. */
function Legend() {
  return (
    <div className="mt-6 border-t border-ink-200 pt-3">
      <p className="text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
        Task status
      </p>
      <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
        {SIGNALS.map((signal) => (
          <li key={signal.id} className="flex items-center gap-1.5 text-xs text-ink-500">
            <Badge tone={signal.tone}>{signal.label}</Badge>
            <span>{signal.legend}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
