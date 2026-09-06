import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  SIGNALS,
  badgeFor,
  counts,
  describeDeadline,
  group,
  isOpen,
  styleFor,
} from "../lib/tasks.ts";
import type { Task } from "../api/types.ts";

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Send the revised proposal",
    description: null,
    status: "todo",
    display_status: "todo",
    urgency: "normal",
    is_overdue: false,
    signal: "todo",
    days_overdue: 0,
    priority: "normal",
    due_at: "2026-09-20T23:59:59Z",
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
    created_at: "2026-09-10T09:00:00Z",
    updated_at: "2026-09-10T09:00:00Z",
    ...overrides,
  };
}

const shown = (iso: string) => iso.slice(0, 10);

describe("the four colours", () => {
  it("gives each signal one tone and one word", () => {
    assert.equal(styleFor("todo").tone, "neutral");
    assert.equal(styleFor("attention").tone, "warning");
    assert.equal(styleFor("urgent").tone, "danger");
    assert.equal(styleFor("done").tone, "positive");
  });

  it("never recomputes the colour from a date", () => {
    // The two-day boundary lives on the server. A task it called `todo` stays
    // grey even with a deadline last week — the alternative is one threshold
    // in two places, disagreeing the first time either is edited.
    const contradictory = task({ signal: "todo", days_overdue: 40 });

    assert.equal(badgeFor(contradictory).tone, "neutral");
  });

  it("says Done for completed work and Cancelled for abandoned work", () => {
    // They share a colour because neither needs attention. They are still
    // different things, and "Done" over abandoned work would claim an
    // achievement nobody made.
    const finished = task({ signal: "done", status: "completed" });
    const dropped = task({ signal: "done", status: "cancelled" });

    assert.equal(badgeFor(finished).label, "Done");
    assert.equal(badgeFor(dropped).label, "Cancelled");
    assert.equal(badgeFor(dropped).tone, badgeFor(finished).tone);
  });

  it("explains every colour in the legend", () => {
    // A colour with no explanation is a colour somebody has to guess at.
    for (const signal of SIGNALS) {
      assert.ok(signal.legend.length > 0, `${signal.id} has no legend`);
    }

    assert.match(styleFor("attention").legend, /two days/i);
    assert.match(styleFor("urgent").legend, /more than two days/i);
  });
});

describe("the line under the title", () => {
  it("says how late rather than when it was due", () => {
    const late = task({ signal: "urgent", days_overdue: 4 });

    assert.equal(describeDeadline(late, shown), "4 days overdue");
  });

  it("uses the singular for one day", () => {
    assert.equal(
      describeDeadline(task({ signal: "todo", days_overdue: 1 }), shown),
      "1 day overdue",
    );
  });

  it("says when it is due while it still is", () => {
    assert.equal(describeDeadline(task(), shown), "Due 2026-09-20");
  });

  it("says there is no deadline rather than inventing one", () => {
    assert.equal(
      describeDeadline(task({ due_at: null, days_overdue: null }), shown),
      "No deadline",
    );
  });

  it("shows a completed task as completed rather than as late", () => {
    const finished = task({
      signal: "done",
      status: "completed",
      days_overdue: 9,
      completed_at: "2026-09-18T10:00:00Z",
    });

    assert.equal(describeDeadline(finished, shown), "Completed 2026-09-18");
  });
});

describe("the list", () => {
  it("puts what needs doing now above what is finished", () => {
    const groups = group([
      task({ id: "done", signal: "done", status: "completed" }),
      task({ id: "todo", signal: "todo" }),
      task({ id: "urgent", signal: "urgent", days_overdue: 5 }),
      task({ id: "attention", signal: "attention", days_overdue: 2 }),
    ]);

    assert.deepEqual(
      groups.map((entry) => entry.signal),
      ["urgent", "attention", "todo", "done"],
    );
  });

  it("drops empty groups rather than showing headings over nothing", () => {
    // Four headings and one task under the last of them reads as three
    // failures.
    const groups = group([task()]);

    assert.deepEqual(
      groups.map((entry) => entry.title),
      ["To do"],
    );
  });

  it("puts the most overdue first within a group", () => {
    const groups = group([
      task({ id: "less", signal: "urgent", days_overdue: 3 }),
      task({ id: "more", signal: "urgent", days_overdue: 11 }),
    ]);

    assert.deepEqual(
      groups[0].tasks.map((entry) => entry.id),
      ["more", "less"],
    );
  });

  it("sorts by deadline within one lateness, undated last", () => {
    const groups = group([
      task({ id: "undated", due_at: null, days_overdue: null }),
      task({ id: "later", due_at: "2026-09-30T00:00:00Z" }),
      task({ id: "sooner", due_at: "2026-09-12T00:00:00Z" }),
    ]);

    assert.deepEqual(
      groups[0].tasks.map((entry) => entry.id),
      ["sooner", "later", "undated"],
    );
  });

  it("keeps the zeroes in the summary strip", () => {
    // A summary that hides its zeroes makes somebody wonder whether the number
    // is zero or missing.
    const strip = counts([task()]);

    assert.equal(strip.length, 4);
    assert.deepEqual(
      strip.map((entry) => entry.count),
      [0, 0, 1, 0],
    );
  });
});

describe("what can still be acted on", () => {
  it("offers Mark done only while there is something to mark", () => {
    assert.equal(isOpen(task()), true);
    assert.equal(isOpen(task({ status: "in_progress" })), true);
    assert.equal(isOpen(task({ status: "completed" })), false);
    assert.equal(isOpen(task({ status: "cancelled" })), false);
  });
});
