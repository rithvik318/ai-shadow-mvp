import type { EmailDigest, ReportEnvelope } from "../../api/types";
import {
  categoryLines,
  describeSender,
  digestOf,
  digestState,
  headline,
} from "../../lib/digest";
import { formatDateTime } from "../../lib/format";
import { Badge, EmptyState } from "../ui";

/**
 * A period of correspondence, as it was.
 *
 * The screen is built around the three states in `lib/digest.ts`, and the
 * distinction it exists to preserve is between "the mailbox was read and held
 * nothing" and "no mailbox was read". Both are quiet; only one of them is a
 * fact about the mail. Showing a row of zeroes for the second would be the
 * single most misleading thing this feature could do.
 *
 * Nothing here classifies. Every category and priority on this screen was
 * decided by triage when somebody ran it, and a message nobody has triaged is
 * shown as untriaged rather than being given a category on the way to the
 * page.
 */
export function DigestView({ envelope }: { envelope: ReportEnvelope }) {
  const state = digestState(envelope);

  if (state === "unavailable") {
    return (
      <EmptyState
        title="No digest for this period"
        description={
          envelope.detail ??
          "This digest could not be produced, and nothing has been made up in its place."
        }
      />
    );
  }

  const digest = digestOf(envelope) as EmailDigest;

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-700">{headline(envelope)}</p>

      {digest.truncated ? (
        <div
          role="status"
          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5"
        >
          <p className="text-sm text-amber-800">
            {digest.truncation_detail ??
              "This period may contain messages that are not counted here."}
          </p>
        </div>
      ) : null}

      {state === "quiet" ? (
        <EmptyState
          title="A quiet period"
          description={`Nothing arrived at ${
            digest.mailbox ?? "this mailbox"
          } and nothing was sent from it in this window.`}
        />
      ) : (
        <>
          <Counts digest={digest} />
          <Categories digest={digest} />
          <Correspondents digest={digest} />
          <Messages
            title="Waiting on a reply"
            empty="Triage found nothing in this period that needs one."
            messages={digest.needs_reply}
          />
          <Messages
            title="Flagged urgent or high priority"
            empty="Triage flagged nothing in this period."
            messages={digest.highlights}
          />
        </>
      )}

      {digest.undated_count > 0 ? (
        <p className="text-xs text-ink-500">
          {digest.undated_count} message
          {digest.undated_count === 1 ? "" : "s"} the mailbox returned without a
          date {digest.undated_count === 1 ? "is" : "are"} counted in no period.
        </p>
      ) : null}
    </div>
  );
}

function Counts({ digest }: { digest: EmailDigest }) {
  const cells: Array<[string, number]> = [
    ["Received", digest.received_count],
    ["Sent", digest.sent_count],
    ["Triaged", digest.triaged_count],
    ["Not yet triaged", digest.untriaged_count],
    ["Needs a reply", digest.needs_reply_count],
    ["Follow-up recommended", digest.follow_up_count],
  ];

  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {cells.map(([label, value]) => (
        <div key={label} className="rounded-md border border-ink-200 px-3 py-2">
          <dt className="text-xs text-ink-500">{label}</dt>
          <dd className="text-lg font-semibold text-ink-900">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Categories({ digest }: { digest: EmailDigest }) {
  const lines = categoryLines(digest);

  if (lines.length === 0) return null;

  return (
    <section>
      <h3 className="text-sm font-semibold text-ink-800">What arrived</h3>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {lines.map((line) => (
          <li key={line.category}>
            <Badge tone={line.tone}>
              {line.label}: {line.count}
            </Badge>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Correspondents({ digest }: { digest: EmailDigest }) {
  if (digest.top_correspondents.length === 0) return null;

  return (
    <section>
      <h3 className="text-sm font-semibold text-ink-800">Who wrote most</h3>
      <ul className="mt-1.5 divide-y divide-ink-100 rounded-md border border-ink-200">
        {digest.top_correspondents.map((person) => (
          <li
            key={person.address}
            className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
          >
            <span className="min-w-0 truncate text-ink-700">
              {person.name ? `${person.name} <${person.address}>` : person.address}
            </span>
            <span className="shrink-0 text-ink-500">
              {person.message_count} message
              {person.message_count === 1 ? "" : "s"}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Messages({
  title,
  empty,
  messages,
}: {
  title: string;
  empty: string;
  messages: EmailDigest["needs_reply"];
}) {
  return (
    <section>
      <h3 className="text-sm font-semibold text-ink-800">{title}</h3>

      {messages.length === 0 ? (
        <p className="mt-1 text-sm text-ink-500">{empty}</p>
      ) : (
        <ul className="mt-1.5 divide-y divide-ink-100 rounded-md border border-ink-200">
          {messages.map((item) => (
            <li key={item.message_id} className="px-3 py-2.5">
              <p className="text-sm font-medium text-ink-900">{item.subject}</p>
              <p className="mt-0.5 text-sm text-ink-600">{describeSender(item)}</p>
              {item.summary ? (
                <p className="mt-1 text-sm leading-relaxed text-ink-600">
                  {item.summary}
                </p>
              ) : null}
              {item.received_at ? (
                <p className="mt-1 text-xs text-ink-500">
                  {formatDateTime(item.received_at)}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
