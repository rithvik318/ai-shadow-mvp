import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  describeDeadline,
  describeEvent,
  describeTask,
  describeTaskStatus,
  hasEscalationTarget,
  isEmpty,
  openTasks,
  signalFor,
  toneFor,
} from "../lib/report.ts";
import type { CalendarEvent, Escalation, Task, WeeklyReport } from "../api/types.ts";

const NOW = Date.parse("2026-09-01T12:00:00Z");

function at(days: number): string {
  return new Date(NOW + days * 86_400_000).toISOString();
}

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: overrides.id ?? "t1",
    title: "Submit project document",
    description: null,
    status: "todo",
    display_status: "todo",
    urgency: "normal",
    is_overdue: false,
    signal: "todo",
    days_overdue: 0,
    priority: "normal",
    due_at: at(10),
    completed_at: null,
    source: "manual",
    source_message_id: null,
    source_thread_id: null,
    contact_name: null,
    contact_address: null,
    contact_display: null,
    escalation_requested: false,
    escalation_note: null,
    escalation_required: false,
    escalation_reason: null,
    escalation_action: null,
    created_at: at(-20),
    updated_at: at(-20),
    ...overrides,
  };
}

function event(overrides: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    id: "e1",
    title: "Design review",
    description: null,
    location: null,
    starts_at: at(-1),
    ends_at: null,
    status: "scheduled",
    display_status: "unknown",
    needs_answer: true,
    is_past: true,
    attendance_evidence: "none",
    attendance_recorded_at: null,
    attendance_note: null,
    organiser_name: null,
    organiser_address: null,
    source: "manual",
    created_at: at(-10),
    updated_at: at(-10),
    ...overrides,
  };
}

function report(overrides: Partial<WeeklyReport> = {}): WeeklyReport {
  return {
    generated_at: at(0),
    period_start: at(-7),
    period_end: at(7),
    completed_count: 0,
    due_soon_count: 0,
    overdue_count: 0,
    escalation_count: 0,
    summary: {
      completed_count: 0,
      due_soon_count: 0,
      overdue_count: 0,
      escalation_count: 0,
      events_needing_answer_count: 0,
    },
    upcoming: [],
    overdue: [],
    completed: [],
    blocked: [],
    follow_ups: [],
    high_priority: [],
    deadlines: [],
    escalations: [],
    calendar_connected: false,
    calendar_detail: "Calendar: no provider connected.",
    events: [],
    events_needing_answer: [],
    ...overrides,
  };
}

describe("severity to colour", () => {
  it("maps the backend's three severities to the report's three colours", () => {
    assert.equal(signalFor("normal"), "green");
    assert.equal(signalFor("warning"), "yellow");
    assert.equal(signalFor("critical"), "red");
  });

  it("shows a comfortable task as green rather than grey", () => {
    // The report's job is to make "nothing to worry about" as readable as
    // "act now". A neutral badge reads as missing information.
    assert.equal(toneFor("normal"), "positive");
    assert.equal(toneFor("warning"), "warning");
    assert.equal(toneFor("critical"), "danger");
  });

  it("never recomputes urgency from a date", () => {
    // The backend owns the threshold. A task it called `normal` stays green
    // even with a deadline tomorrow — the alternative is two copies of one
    // threshold, disagreeing the first time either is edited.
    const soon = task({ due_at: at(1), urgency: "normal" });

    assert.equal(describeTask(soon, NOW).signal, "green");
  });
});

describe("the one-line brief", () => {
  it("reads the way the report is meant to be scanned", () => {
    const line = describeTask(
      task({ title: "Approval pending", urgency: "critical", due_at: at(-4) }),
      NOW,
    );

    assert.equal(line.summary, "[RED] Approval pending — Overdue by 4 days");
  });

  it("says how long is left when a deadline is close", () => {
    const line = describeTask(
      task({ title: "Follow up with client", urgency: "warning", due_at: at(2) }),
      NOW,
    );

    assert.equal(line.summary, "[YELLOW] Follow up with client — Due in 2 days");
  });

  it("shows a completed task as completed rather than as late", () => {
    // Saying "overdue by 4 days" beside "Completed" reads as a contradiction.
    const line = describeTask(
      task({ status: "completed", display_status: "completed", due_at: at(-4) }),
      NOW,
    );

    assert.equal(line.deadline, "Completed");
    assert.equal(line.summary, "[GREEN] Submit project document — Completed");
  });
});

describe("deadlines in words", () => {
  it("rounds lateness away from now", () => {
    // Under-reporting lateness defeats the report's purpose, so 1.2 days late
    // reads as 2 rather than 1.
    assert.equal(describeDeadline(at(-1.2), NOW), "Overdue by 2 days");
  });

  it("uses the singular for one day", () => {
    assert.equal(describeDeadline(at(-0.5), NOW), "Overdue by 1 day");
    assert.equal(describeDeadline(at(1.5), NOW), "Due in 2 days");
  });

  it("says today rather than in 0 days", () => {
    assert.equal(describeDeadline(at(0.4), NOW), "Due today");
  });

  it("says there is no deadline rather than inventing one", () => {
    assert.equal(describeDeadline(null, NOW), "No deadline");
    assert.equal(describeDeadline("not a date", NOW), "No deadline");
  });
});

describe("status labels", () => {
  it("names every state the backend can send", () => {
    assert.equal(describeTaskStatus("todo"), "To do");
    assert.equal(describeTaskStatus("in_progress"), "In progress");
    assert.equal(describeTaskStatus("overdue"), "Overdue");
    assert.equal(describeTaskStatus("escalation_required"), "Escalation required");
  });
});

describe("events", () => {
  it("shows an unanswered past meeting as not recorded, not as missed", () => {
    // The refusal, carried into the UI: a meeting existing is not evidence
    // that anybody went, and colouring it red would imply it was missed.
    const line = describeEvent(event());

    assert.equal(line.statusLabel, "Not recorded");
    assert.equal(line.tone, "neutral");
    assert.equal(line.needsAnswer, true);
  });

  it("shows an answered meeting in its own colour", () => {
    assert.equal(
      describeEvent(event({ display_status: "attended", needs_answer: false })).tone,
      "positive",
    );
    assert.equal(
      describeEvent(event({ display_status: "missed", needs_answer: false })).tone,
      "danger",
    );
  });

  it("does not ask about a meeting that has not happened", () => {
    const upcoming = event({
      starts_at: at(2),
      display_status: "scheduled",
      needs_answer: false,
      is_past: false,
    });

    assert.equal(describeEvent(upcoming).needsAnswer, false);
  });
});

describe("escalation targets", () => {
  const base: Escalation = {
    task: task(),
    reason: "High-priority task is overdue",
    age_seconds: 400000,
    recommended_action: "Prepare an escalation email for review.",
    contact_name: "Robert Keenan",
    contact_address: "Robert.Keenan@sunradia.com",
    escalation_contact_name: null,
    escalation_contact_address: null,
    escalation_contact_display: "Escalation target not identified",
    target_source: "not_identified",
  };

  it("offers to prepare an email only when a target is configured", () => {
    assert.equal(hasEscalationTarget(base), false);
    assert.equal(
      hasEscalationTarget({
        ...base,
        escalation_contact_address: "sudha@sunradia.com",
        target_source: "configured",
      }),
      true,
    );
  });

  it("never treats the counterparty as a target", () => {
    // They are usually the person who has not replied. Escalating to them is
    // another follow-up wearing a different hat.
    assert.equal(base.contact_address, "Robert.Keenan@sunradia.com");
    assert.equal(hasEscalationTarget(base), false);
  });
});

describe("the flat list of open work", () => {
  it("shows a task once even though the sections overlap", () => {
    // An overdue high-priority follow-up is in three sections by design.
    const late = task({ id: "t1", urgency: "critical", due_at: at(-2) });
    const flat = openTasks(
      report({ overdue: [late], blocked: [], upcoming: [late] }),
      NOW,
    );

    assert.equal(flat.length, 1);
  });

  it("puts the most severe first", () => {
    const flat = openTasks(
      report({
        upcoming: [
          task({ id: "calm", urgency: "normal", due_at: at(9) }),
          task({ id: "soon", urgency: "warning", due_at: at(2) }),
        ],
        overdue: [task({ id: "late", urgency: "critical", due_at: at(-1) })],
      }),
      NOW,
    );

    assert.deepEqual(
      flat.map((line) => line.task.id),
      ["late", "soon", "calm"],
    );
  });

  it("sorts by deadline within one severity, undated last", () => {
    const flat = openTasks(
      report({
        upcoming: [
          task({ id: "undated", urgency: "normal", due_at: null }),
          task({ id: "later", urgency: "normal", due_at: at(9) }),
          task({ id: "sooner", urgency: "normal", due_at: at(5) }),
        ],
      }),
      NOW,
    );

    assert.deepEqual(
      flat.map((line) => line.task.id),
      ["sooner", "later", "undated"],
    );
  });
});

describe("an empty week", () => {
  it("is distinguishable from a report that failed to load", () => {
    assert.equal(isEmpty(report()), true);
    assert.equal(isEmpty(report({ upcoming: [task()] })), false);
    assert.equal(isEmpty(report({ events: [event()] })), false);
  });
});
