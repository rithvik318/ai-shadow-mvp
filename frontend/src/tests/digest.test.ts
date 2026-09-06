import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  REPORT_TYPES,
  categoryLines,
  describeCategory,
  describePeriod,
  describeProvenance,
  describeSender,
  digestOf,
  digestState,
  headline,
  needsMailbox,
  toneForCategory,
} from "../lib/digest.ts";
import type {
  DigestMessage,
  EmailDigest,
  ReportEnvelope,
  ReportPeriod,
} from "../api/types.ts";

function period(overrides: Partial<ReportPeriod> = {}): ReportPeriod {
  return {
    kind: "week",
    key: "2026-08-24",
    label: "24 Aug – 30 Aug 2026",
    start: "2026-08-24T00:00:00Z",
    end: "2026-08-31T00:00:00Z",
    is_complete: true,
    ...overrides,
  };
}

function digest(overrides: Partial<EmailDigest> = {}): EmailDigest {
  return {
    mailbox: "sudha@sunradia.com",
    received_count: 0,
    sent_count: 0,
    triaged_count: 0,
    untriaged_count: 0,
    needs_reply_count: 0,
    follow_up_count: 0,
    undated_count: 0,
    by_category: {},
    by_priority: {},
    top_correspondents: [],
    needs_reply: [],
    highlights: [],
    is_quiet: true,
    truncated: false,
    truncation_detail: null,
    ...overrides,
  };
}

function envelope(overrides: Partial<ReportEnvelope> = {}): ReportEnvelope {
  return {
    report_type: "weekly_email_digest",
    status: "complete",
    period: period(),
    generated_at: "2026-08-31T00:05:00Z",
    is_provisional: false,
    from_history: false,
    detail: null,
    content: digest(),
    ...overrides,
  };
}

function message(overrides: Partial<DigestMessage> = {}): DigestMessage {
  return {
    message_id: "m1",
    subject: "Proposal follow-up",
    sender_name: null,
    sender_address: "client@example.com",
    received_at: "2026-08-26T09:00:00Z",
    category: "untriaged",
    priority: null,
    summary: null,
    needs_reply: false,
    ...overrides,
  };
}

describe("the three states a digest can be in", () => {
  it("tells 'no mailbox was read' apart from 'nothing arrived'", () => {
    // The whole feature turns on this. A person who has never connected a
    // mailbox must not be shown a quiet week.
    const refused = envelope({
      status: "unavailable",
      content: {},
      detail: "No mailbox is connected for this user.",
    });

    assert.equal(digestState(refused), "unavailable");
    assert.equal(digestState(envelope()), "quiet");
    assert.equal(
      digestState(envelope({ content: digest({ received_count: 4 }) })),
      "populated",
    );
  });

  it("treats a missing report as unavailable rather than as empty", () => {
    assert.equal(digestState(null), "unavailable");
  });

  it("never reports counts for a report that could not be produced", () => {
    const refused = envelope({ status: "unavailable", content: {} });

    assert.equal(digestOf(refused), null);
  });

  it("does not read a work report as a digest", () => {
    // The envelope is shared; the body is not. Reading a `WeeklyReport` as a
    // digest would produce undefined counts rendered as numbers.
    const work = envelope({ report_type: "weekly_work", content: { upcoming: [] } });

    assert.equal(digestOf(work), null);
  });
});

describe("the headline", () => {
  it("says why rather than saying zero when nothing could be read", () => {
    const refused = envelope({
      status: "unavailable",
      content: {},
      detail: "Mail.Read has not been consented for this application.",
    });

    assert.equal(headline(refused), "Mail.Read has not been consented for this application.");
  });

  it("says the period was quiet when the mailbox was actually read", () => {
    assert.match(headline(envelope()), /No messages arrived/);
  });

  it("counts what arrived and what was sent", () => {
    const busy = envelope({
      content: digest({ received_count: 12, sent_count: 3, is_quiet: false }),
    });

    assert.equal(headline(busy), "12 received, 3 sent.");
  });

  it("mentions outstanding triage only when there is some", () => {
    const partly = envelope({
      content: digest({
        received_count: 12,
        sent_count: 3,
        untriaged_count: 5,
        is_quiet: false,
      }),
    });

    assert.equal(headline(partly), "12 received, 3 sent, 5 not yet triaged.");
  });
});

describe("categories", () => {
  it("names an unclassified message as unclassified", () => {
    // Not as an FYI. Nothing has read it.
    assert.equal(describeCategory("untriaged"), "Not yet triaged");
    assert.equal(toneForCategory("untriaged"), "neutral");
  });

  it("gives urgent and needs-reply the colours triage already decided", () => {
    assert.equal(toneForCategory("urgent"), "danger");
    assert.equal(toneForCategory("needs_reply"), "warning");
    assert.equal(toneForCategory("fyi"), "positive");
  });

  it("puts the largest category first and untriaged last", () => {
    const lines = categoryLines(
      digest({
        by_category: { untriaged: 40, fyi: 3, needs_reply: 9 },
      }),
    );

    assert.deepEqual(
      lines.map((line) => line.category),
      ["needs_reply", "fyi", "untriaged"],
    );
  });

  it("keeps an unknown category rather than dropping it", () => {
    // A category this build has not heard of is still something that arrived.
    const lines = categoryLines(digest({ by_category: { newsletter: 2 } }));

    assert.equal(lines[0].label, "newsletter");
    assert.equal(lines[0].tone, "neutral");
  });
});

describe("senders", () => {
  it("shows a name with its address when both are known", () => {
    assert.equal(
      describeSender(message({ sender_name: "Robert Keenan" })),
      "Robert Keenan <client@example.com>",
    );
  });

  it("says the sender is unknown rather than showing an empty string", () => {
    assert.equal(
      describeSender(message({ sender_name: null, sender_address: null })),
      "Unknown sender",
    );
  });
});

describe("periods and provenance", () => {
  it("marks a period that has not finished", () => {
    assert.equal(describePeriod(period()), "24 Aug – 30 Aug 2026");
    assert.match(describePeriod(period({ is_complete: false })), /in progress/);
  });

  it("explains why a past report does not move", () => {
    assert.match(describeProvenance(envelope({ from_history: true })), /will not change/);
    assert.match(
      describeProvenance(envelope({ is_provisional: true })),
      /still running/,
    );
  });
});

describe("what each report needs", () => {
  it("knows which reports are impossible without a mailbox", () => {
    assert.equal(needsMailbox("weekly_email_digest"), true);
    assert.equal(needsMailbox("monthly_email_digest"), true);
  });

  it("leads with the documents and lists the work report last", () => {
    // Reports answers "what happened". The work report is still reachable —
    // it holds the meetings and escalation actions — but it opening the
    // section is what made this read as a task dashboard.
    assert.deepEqual(
      REPORT_TYPES.map((entry) => entry.id),
      ["weekly_email_digest", "monthly_email_digest", "weekly_work"],
    );
    assert.equal(needsMailbox("weekly_work"), false);
  });
});
