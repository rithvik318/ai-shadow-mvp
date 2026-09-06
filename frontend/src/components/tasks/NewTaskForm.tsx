import { type FormEvent, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { TaskPriority } from "../../api/types";
import { Button, ErrorNotice } from "../ui";

const PRIORITIES: TaskPriority[] = ["low", "normal", "high", "urgent"];

/**
 * Adding a task by hand.
 *
 * Four fields, and the shortness is the design. The model has a dozen columns —
 * source provenance, contact name and address, escalation notes, thread ids —
 * and every one of them exists to carry something a *triaged email* knew.
 * Asking a person to fill them in would be asking them to do the Email Agent's
 * job, so the form takes what somebody typing a reminder actually has: what it
 * is, a sentence about it, when it is due, and how much it matters.
 *
 * The due date is optional, and stays optional. A required deadline would mean
 * every hand-written task carries a date somebody invented to get past the
 * form — and that date would then drive a colour, and eventually an escalation.
 */
export function NewTaskForm({
  userId,
  onCreated,
  onCancel,
}: {
  userId: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("normal");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!title.trim()) return;

    setSaving(true);
    setError(null);

    try {
      await api.createTask(userId, {
        title: title.trim(),
        description: description.trim() || null,
        // A date input gives a day, not an instant. End of day in the
        // browser's own zone is what somebody means by "due Friday" — sending
        // midnight would make a task due Friday overdue on Friday morning.
        due_at: dueDate ? new Date(`${dueDate}T23:59:59`).toISOString() : null,
        priority,
      });

      onCreated();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The task was not created.",
      );
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={(event) => void submit(event)}
      className="rounded-md border border-ink-200 bg-white px-4 py-3.5"
    >
      <div className="space-y-3">
        <div>
          <label htmlFor="task-title" className="block text-xs font-medium text-ink-600">
            What needs doing
          </label>
          <input
            id="task-title"
            type="text"
            autoFocus
            required
            maxLength={500}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Send the revised proposal"
            className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
          />
        </div>

        <div>
          <label
            htmlFor="task-description"
            className="block text-xs font-medium text-ink-600"
          >
            Summary <span className="font-normal text-ink-400">(optional)</span>
          </label>
          <textarea
            id="task-description"
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="A sentence about what this involves."
            className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
          />
        </div>

        <div className="flex flex-wrap gap-3">
          <div>
            <label htmlFor="task-due" className="block text-xs font-medium text-ink-600">
              Due <span className="font-normal text-ink-400">(optional)</span>
            </label>
            <input
              id="task-due"
              type="date"
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
              className="mt-1 rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            />
          </div>

          <div>
            <label
              htmlFor="task-priority"
              className="block text-xs font-medium text-ink-600"
            >
              Priority
            </label>
            <select
              id="task-priority"
              value={priority}
              onChange={(event) => setPriority(event.target.value as TaskPriority)}
              className="mt-1 rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            >
              {PRIORITIES.map((value) => (
                <option key={value} value={value}>
                  {value[0].toUpperCase() + value.slice(1)}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {error ? (
        <div className="mt-3">
          <ErrorNotice message={error} />
        </div>
      ) : null}

      <div className="mt-3 flex justify-end gap-2">
        <Button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={saving || !title.trim()}>
          {saving ? "Adding…" : "Add task"}
        </Button>
      </div>
    </form>
  );
}
