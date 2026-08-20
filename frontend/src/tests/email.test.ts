/**
 * Email Agent logic.
 *
 * The suites that matter are the send gate and the status presentation: those
 * are the two places where a UI could start claiming an email went out when it
 * did not.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  REVISION_OPERATIONS,
  approvalBlockers,
  categoryRank,
  describeCategory,
  describeDraftStatus,
  describeProvider,
  groupByCategory,
  invalidRecipients,
  isAwaitingReview,
  isEditable,
  isOverdue,
  isPlausibleAddress,
  missingPlaceholders,
  operationBlocker,
  parseRecipients,
  partitionFollowUps,
  searchTemplates,
  sendBlockers,
} from "../lib/email.ts";
import type {
  EmailAssessment,
  EmailCategory,
  EmailDraft,
  EmailDraftStatus,
  EmailProviderStatus,
  EmailTemplate,
} from "../api/types.ts";

const NOW = new Date("2026-08-19T12:00:00Z").getTime();

function draft(overrides: Partial<EmailDraft> = {}): EmailDraft {
  return {
    id: "d1",
    to_recipients: ["client@example.com"],
    cc_recipients: [],
    bcc_recipients: [],
    subject: "Following up",
    body: "Just checking in.",
    status: "draft",
    generated_by_ai: false,
    template_id: null,
    in_reply_to_message_id: null,
    provider: null,
    provider_message_id: null,
    provider_thread_id: null,
    approved_at: null,
    sent_at: null,
    send_error: null,
    attachments: [],
    created_at: "2026-08-19T09:00:00Z",
    updated_at: "2026-08-19T09:00:00Z",
    ...overrides,
  };
}

function provider(overrides: Partial<EmailProviderStatus> = {}): EmailProviderStatus {
  return {
    provider: "outlook",
    configured: true,
    connected: true,
    mailbox: "shared@sunradia.com",
    detail: null,
    capabilities: [],
    ...overrides,
  };
}

function assessment(overrides: Partial<EmailAssessment> = {}): EmailAssessment {
  return {
    id: "a1",
    provider: "outlook",
    provider_message_id: "m1",
    provider_thread_id: "t1",
    subject: "Revised numbers",
    sender: "client@example.com",
    received_at: "2026-08-18T09:00:00Z",
    category: "needs_reply",
    priority: "high",
    summary: "They want the numbers.",
    suggested_action: "Send them today.",
    action_items: [],
    follow_up_recommended: true,
    follow_up_reason: "They are waiting.",
    follow_up_due_at: null,
    handled: false,
    assessed_at: "2026-08-18T10:00:00Z",
    ...overrides,
  };
}

function template(overrides: Partial<EmailTemplate> = {}): EmailTemplate {
  return {
    id: "t1",
    name: "Introduction",
    description: "For new prospects",
    category: "introduction",
    subject_template: "Hello from {{ company }}",
    body_template: "Hi {{ name }},",
    placeholders: ["company", "name"],
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

describe("draft status", () => {
  it("only ever labels a draft Sent when the backend says sent", () => {
    // The load-bearing assertion in this file. `sent` is written by the
    // backend only on a provider receipt, so if no other status maps to
    // "Sent", the UI cannot claim an email went out that did not.
    const statuses: EmailDraftStatus[] = [
      "draft",
      "needs_review",
      "approved",
      "sending",
      "sent",
      "failed",
    ];

    const sent = statuses.filter(
      (status) => describeDraftStatus(status).label === "Sent",
    );

    assert.deepEqual(sent, ["sent"]);
  });

  it("distinguishes approved from sent", () => {
    assert.equal(describeDraftStatus("approved").label, "Ready to send");
    assert.notEqual(describeDraftStatus("approved").tone, "positive");
  });

  it("marks a generated draft as awaiting review", () => {
    assert.equal(isAwaitingReview(draft({ status: "needs_review" })), true);
    assert.equal(isAwaitingReview(draft({ status: "approved" })), false);
  });

  it("refuses to edit a draft that has left or is leaving", () => {
    assert.equal(isEditable(draft({ status: "sent" })), false);
    assert.equal(isEditable(draft({ status: "sending" })), false);
    assert.equal(isEditable(draft({ status: "failed" })), true);
  });
});

describe("the send gate", () => {
  it("allows sending only an approved draft with a connected mailbox", () => {
    const ready = draft({ status: "approved", approved_at: "2026-08-19T10:00:00Z" });

    assert.deepEqual(sendBlockers(ready, provider()), []);
  });

  it("blocks a draft nobody approved", () => {
    const blockers = sendBlockers(draft(), provider());

    assert.ok(blockers.some((item) => item.includes("approve")));
  });

  it("blocks a generated draft that has not been read", () => {
    const blockers = sendBlockers(draft({ status: "needs_review" }), provider());

    assert.ok(blockers.some((item) => item.includes("approve")));
  });

  it("blocks sending when no mailbox is connected", () => {
    const ready = draft({ status: "approved" });
    const blockers = sendBlockers(ready, provider({ connected: false }));

    assert.ok(blockers.some((item) => item.includes("Connect a mailbox")));
  });

  it("blocks sending when the provider status has not loaded", () => {
    // Fail closed. An unknown connection state must not render an enabled
    // Send button.
    const ready = draft({ status: "approved" });

    assert.ok(sendBlockers(ready, null).length > 0);
  });

  it("names every missing piece rather than only the first", () => {
    const empty = draft({ to_recipients: [], subject: " ", body: "" });
    const blockers = sendBlockers(empty, provider());

    assert.equal(blockers.length, 4);
  });

  it("says a sent email has already gone rather than listing fixes", () => {
    assert.deepEqual(sendBlockers(draft({ status: "sent" }), provider()), [
      "This email has already been sent.",
    ]);
  });

  it("requires recipients, subject and body before approval", () => {
    assert.deepEqual(approvalBlockers(draft()), []);
    assert.equal(approvalBlockers(draft({ to_recipients: [] })).length, 1);
    assert.equal(approvalBlockers(draft({ subject: "  " })).length, 1);
    assert.equal(approvalBlockers(draft({ body: "" })).length, 1);
  });

  it("does not require a mailbox to approve", () => {
    // Approval is a judgement about text. It is useful before a mailbox
    // exists, and blocking it would make the workspace useless until Outlook
    // is connected.
    assert.deepEqual(approvalBlockers(draft()), []);
  });
});

describe("recipients", () => {
  it("splits on commas, semicolons and newlines", () => {
    assert.deepEqual(parseRecipients("a@x.com, b@x.com; c@x.com\nd@x.com"), [
      "a@x.com",
      "b@x.com",
      "c@x.com",
      "d@x.com",
    ]);
  });

  it("removes duplicates case-insensitively and keeps order", () => {
    assert.deepEqual(parseRecipients("b@x.com, A@X.com, a@x.com"), [
      "b@x.com",
      "A@X.com",
    ]);
  });

  it("ignores empty fragments from trailing separators", () => {
    assert.deepEqual(parseRecipients("a@x.com, , ;\n"), ["a@x.com"]);
  });

  it("accepts plausible addresses and rejects obvious mistakes", () => {
    assert.equal(isPlausibleAddress("a.b+tag@sub.example.co.uk"), true);
    assert.equal(isPlausibleAddress("first.last@example-company.com"), true);
    assert.equal(isPlausibleAddress("jsmith"), false);
    assert.equal(isPlausibleAddress("a@x"), false);
    assert.equal(isPlausibleAddress("a@.com"), false);
    assert.equal(isPlausibleAddress("a@b@c.com"), false);
  });

  it("rejects whitespace anywhere, exactly as the backend does", () => {
    // A UI that accepted these would send a request the API refuses, and the
    // person would get a 422 instead of an inline hint.
    assert.equal(isPlausibleAddress("john smith@x.com"), false);
    assert.equal(isPlausibleAddress("a@x .com"), false);
    assert.equal(isPlausibleAddress("a\tb@x.com"), false);
    assert.equal(isPlausibleAddress("a@x.com b@x.com"), false);
  });

  it("reports which addresses a person needs to fix", () => {
    assert.deepEqual(invalidRecipients(["ok@x.com", "nope", "also@y.com"]), ["nope"]);
  });
});

describe("triage grouping", () => {
  it("orders categories by how much they demand", () => {
    const order: EmailCategory[] = [
      "urgent",
      "needs_reply",
      "follow_up",
      "fyi",
      "low_priority",
    ];

    const ranks = order.map(categoryRank);

    assert.deepEqual(ranks, [...ranks].sort((a, b) => a - b));
  });

  it("sorts untriaged last, not first", () => {
    // A message nobody has judged is unknown, not unimportant. Putting
    // unknowns above known-urgent ones would bury the urgent ones.
    assert.ok(categoryRank(null) > categoryRank("low_priority"));
  });

  it("groups rows and drops empty categories", () => {
    const rows = [
      { id: 1, category: "urgent" as EmailCategory | null },
      { id: 2, category: null },
      { id: 3, category: "urgent" as EmailCategory | null },
    ];

    const groups = groupByCategory(rows, (row) => row.category);

    assert.deepEqual(
      groups.map((group) => group.label),
      ["Urgent", "Not yet triaged"],
    );
    assert.equal(groups[0].items.length, 2);
  });

  it("gives every category a label and a tone", () => {
    for (const category of [
      "urgent",
      "needs_reply",
      "follow_up",
      "fyi",
      "low_priority",
    ] as EmailCategory[]) {
      assert.ok(describeCategory(category).label);
      assert.ok(describeCategory(category).tone);
    }
  });
});

describe("follow-ups", () => {
  it("treats an undated follow-up as outstanding, never overdue", () => {
    // The backend never invents a due date, so there is nothing to be late
    // against.
    assert.equal(isOverdue(assessment({ follow_up_due_at: null }), NOW), false);
  });

  it("marks a passed due date overdue", () => {
    assert.equal(
      isOverdue(assessment({ follow_up_due_at: "2026-08-18T09:00:00Z" }), NOW),
      true,
    );
  });

  it("does not mark a handled follow-up overdue", () => {
    assert.equal(
      isOverdue(
        assessment({ follow_up_due_at: "2026-08-18T09:00:00Z", handled: true }),
        NOW,
      ),
      false,
    );
  });

  it("ignores an unparsable date rather than treating it as the epoch", () => {
    assert.equal(
      isOverdue(assessment({ follow_up_due_at: "next Friday-ish" }), NOW),
      false,
    );
  });

  it("partitions overdue from the rest without reordering either", () => {
    const rows = [
      assessment({ id: "a1", follow_up_due_at: "2026-08-18T09:00:00Z" }),
      assessment({ id: "a2", follow_up_due_at: null }),
      assessment({ id: "a3", follow_up_due_at: "2026-08-17T09:00:00Z" }),
    ];

    const { overdue, upcoming } = partitionFollowUps(rows, NOW);

    assert.deepEqual(
      overdue.map((item) => item.id),
      ["a1", "a3"],
    );
    assert.deepEqual(
      upcoming.map((item) => item.id),
      ["a2"],
    );
  });
});

describe("templates", () => {
  it("reports which placeholders are still empty", () => {
    assert.deepEqual(missingPlaceholders(template(), { company: "SunRadia" }), [
      "name",
    ]);
  });

  it("treats whitespace as unfilled", () => {
    assert.deepEqual(
      missingPlaceholders(template(), { company: "  ", name: "Ana" }),
      ["company"],
    );
  });

  it("searches name, description and category", () => {
    const templates = [template(), template({ id: "t2", name: "Thanks", description: null, category: "thank_you", placeholders: [] })];

    assert.equal(searchTemplates(templates, "prospect").length, 1);
    assert.equal(searchTemplates(templates, "thank").length, 1);
    assert.equal(searchTemplates(templates, "   ").length, 2);
  });
});

describe("revision operations", () => {
  it("every revision operation needs an existing body", () => {
    assert.ok(REVISION_OPERATIONS.every((choice) => choice.needsBody));
  });

  it("blocks a revision with nothing to revise", () => {
    const shorten = REVISION_OPERATIONS.find(
      (choice) => choice.operation === "shorten",
    )!;

    assert.ok(operationBlocker(shorten, { body: "   ", tone: "" }));
    assert.equal(operationBlocker(shorten, { body: "Some text.", tone: "" }), null);
  });

  it("blocks a tone change with no tone", () => {
    const tone = REVISION_OPERATIONS.find(
      (choice) => choice.operation === "change_tone",
    )!;

    assert.ok(operationBlocker(tone, { body: "Some text.", tone: "" }));
    assert.equal(
      operationBlocker(tone, { body: "Some text.", tone: "warmer" }),
      null,
    );
  });
});

describe("provider status", () => {
  it("never implies a mailbox exists when none is configured", () => {
    assert.equal(describeProvider(null).label, "Not connected");
    assert.equal(describeProvider(provider({ configured: false })).label, "Not connected");
  });

  it("separates not-configured from failed-to-connect", () => {
    // The two need different remedies, and one label for both would send
    // somebody to the wrong page.
    assert.equal(
      describeProvider(provider({ connected: false })).label,
      "Connection failed",
    );
  });

  it("reports a working connection", () => {
    assert.equal(describeProvider(provider()).tone, "positive");
  });
});
