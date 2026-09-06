/**
 * Reading an email digest without adding to it.
 *
 * The rule these functions exist to hold: **the UI never turns an absence into
 * a number.** Three states look similar on screen and mean completely
 * different things, and collapsing any two of them is the failure this module
 * is written against:
 *
 * - `unavailable` — no mailbox was read. The server says why.
 * - quiet — the mailbox was read and held nothing.
 * - untriaged — messages arrived and nobody has classified them.
 *
 * The third is the easiest to get wrong: an untriaged message is not an FYI,
 * and rendering it as one would put a category on a message no classifier ever
 * saw. So `untriaged` is carried through as its own label everywhere.
 *
 * Everything here is a pure function over the API's shapes. No fetching, no
 * state, no dates read from the local clock unless one is passed in — which is
 * what makes it testable without a browser.
 */

import type {
  DigestMessage,
  EmailDigest,
  ReportEnvelope,
  ReportPeriod,
  ReportType,
} from "../api/types";

export const REPORT_TYPES: Array<{
  id: ReportType;
  label: string;
  hint: string;
  /** Whether this report needs a connected mailbox to say anything at all. */
  needsMailbox: boolean;
}> = [
  // The two Activity Reports lead, because Reports answers "what happened".
  // The work report is still reachable — it holds the meetings-needing-an-
  // answer and escalation actions, which live nowhere else — but it is listed
  // last and named for what it is. It used to be first, which is what made
  // this section read as a task dashboard rather than a set of documents.
  {
    id: "weekly_email_digest",
    label: "Weekly Activity Report",
    hint: "What happened this week",
    needsMailbox: true,
  },
  {
    id: "monthly_email_digest",
    label: "Monthly Activity Report",
    hint: "What happened this month",
    needsMailbox: true,
  },
  {
    id: "weekly_work",
    label: "This week's work",
    hint: "Meetings needing an answer, and escalations",
    needsMailbox: false,
  },
];

export function describeReportType(reportType: ReportType): string {
  return REPORT_TYPES.find((item) => item.id === reportType)?.label ?? reportType;
}

export function needsMailbox(reportType: ReportType): boolean {
  return REPORT_TYPES.find((item) => item.id === reportType)?.needsMailbox ?? false;
}

/** The digest body, or null when this envelope does not carry one. */
export function digestOf(envelope: ReportEnvelope | null): EmailDigest | null {
  if (envelope === null || envelope.status !== "complete") return null;
  if (!needsMailbox(envelope.report_type)) return null;

  const content = envelope.content;

  if (content === null || typeof content !== "object") return null;
  if (!("received_count" in content)) return null;

  return content as EmailDigest;
}

export type DigestState =
  | "unavailable"
  | "quiet"
  | "populated";

/**
 * Which of the three states this envelope is in.
 *
 * Kept as one function so a component cannot accidentally test only two of
 * them — the bug being guarded against is a screen that renders "0 messages"
 * for somebody who has never connected a mailbox.
 */
export function digestState(envelope: ReportEnvelope | null): DigestState {
  if (envelope === null || envelope.status === "unavailable") return "unavailable";

  const digest = digestOf(envelope);

  if (digest === null) return "unavailable";

  return digest.received_count === 0 && digest.sent_count === 0
    ? "quiet"
    : "populated";
}

const CATEGORY_LABELS: Record<string, string> = {
  urgent: "Urgent",
  needs_reply: "Needs reply",
  fyi: "FYI",
  follow_up: "Follow up",
  low_priority: "Low priority",
  untriaged: "Not yet triaged",
};

export function describeCategory(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
}

export type Tone = "positive" | "warning" | "danger" | "neutral";

/**
 * The colour a category carries.
 *
 * `untriaged` is neutral on purpose. Any other tone would be an opinion about
 * a message nothing has read.
 */
export function toneForCategory(category: string): Tone {
  switch (category) {
    case "urgent":
      return "danger";
    case "needs_reply":
    case "follow_up":
      return "warning";
    case "fyi":
    case "low_priority":
      return "positive";
    default:
      return "neutral";
  }
}

export interface CategoryLine {
  category: string;
  label: string;
  count: number;
  tone: Tone;
}

/** Categories as a list, most numerous first, untriaged always last.
 *
 * Untriaged is pinned to the end rather than sorted by count because it is not
 * a peer of the others: it is the part of the period nobody has looked at, and
 * a large untriaged count sitting at the top would read as the dominant
 * *kind* of mail rather than as work outstanding.
 */
export function categoryLines(digest: EmailDigest): CategoryLine[] {
  return Object.entries(digest.by_category)
    .map(([category, count]) => ({
      category,
      label: describeCategory(category),
      count,
      tone: toneForCategory(category),
    }))
    .sort((left, right) => {
      if (left.category === "untriaged") return 1;
      if (right.category === "untriaged") return -1;
      if (right.count !== left.count) return right.count - left.count;

      return left.label.localeCompare(right.label);
    });
}

/** How a correspondent is shown — never what would be sent to. */
export function describeSender(message: DigestMessage): string {
  if (message.sender_name && message.sender_address) {
    return `${message.sender_name} <${message.sender_address}>`;
  }

  return message.sender_address ?? message.sender_name ?? "Unknown sender";
}

/**
 * The one-line headline for a digest.
 *
 * Written so that the sentence is true in every state: it never says "0
 * messages" for a period nobody could read, and it never implies triage ran
 * over messages it did not.
 */
export function headline(envelope: ReportEnvelope | null): string {
  const state = digestState(envelope);

  if (state === "unavailable") {
    return envelope?.detail ?? "This digest could not be produced.";
  }

  if (state === "quiet") {
    return "No messages arrived or were sent in this period.";
  }

  const digest = digestOf(envelope) as EmailDigest;
  const received = `${digest.received_count} received`;
  const sent = `${digest.sent_count} sent`;
  const outstanding =
    digest.untriaged_count > 0 ? `, ${digest.untriaged_count} not yet triaged` : "";

  return `${received}, ${sent}${outstanding}.`;
}

/**
 * How a period is described in the selector.
 *
 * A period still running is marked, because a report over one is a partial
 * answer and a person comparing it against a finished week should be able to
 * see that without reading the timestamps.
 */
export function describePeriod(period: ReportPeriod): string {
  return period.is_complete ? period.label : `${period.label} (in progress)`;
}

/**
 * What to say about when this report was produced.
 *
 * The distinction that matters: a report read back from history does not move
 * when the underlying work does, and somebody looking at an old week needs to
 * know that rather than concluding the page is stale.
 */
export function describeProvenance(envelope: ReportEnvelope): string {
  if (envelope.is_provisional) {
    return "This period is still running, so these figures will change.";
  }

  if (envelope.from_history) {
    return "Saved when this period closed. It will not change.";
  }

  return "Saved just now, and will not change from here.";
}
