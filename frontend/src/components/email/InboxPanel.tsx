import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type {
  EmailDraft,
  EmailProviderStatus,
  TriagedMessage,
} from "../../api/types";
import {
  describeCategory,
  describePriority,
  groupByCategory,
} from "../../lib/email";
import { formatDateTime } from "../../lib/format";
import { Badge, Button, EmptyState, ErrorNotice, Spinner } from "../ui";

/**
 * Inbox and triage.
 *
 * The important behaviour here is what it does *not* do. With no mailbox
 * connected it shows a setup notice and no messages — there is no sample data,
 * no placeholder inbox and no illustrative thread. Every row on this screen
 * came from a real mailbox.
 *
 * Triage is per message, on request. Assessing costs a model call, so running
 * it over an inbox on sight would spend real money to label mail nobody asked
 * about. An untriaged row says "Not yet triaged", which is different from
 * "unimportant" and is shown as such.
 */
interface Props {
  userId: string;
  provider: EmailProviderStatus | null;
  onReply: (draft: EmailDraft) => void;
}

/** The first page, and how much further each press reaches.
 *
 * Fifty rather than twenty-five: twenty-five is under a screenful for anybody
 * with real correspondence, and a Load more button on the second row of a list
 * is not a page. The ceiling is the server's — a request above it gets the
 * server's answer, not an error — so this stops offering to load more once the
 * mailbox has returned fewer messages than were asked for.
 */
const INITIAL_PAGE = 50;
const PAGE_STEP = 50;
const MAX_PAGE = 200;

export function InboxPanel({ userId, provider, onReply }: Props) {
  const [rows, setRows] = useState<TriagedMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // How many messages this view has asked for. The provider contract takes a
  // count rather than a cursor, so "load more" re-asks for a larger page. That
  // is honest at these sizes; a real cursor is a change to the provider
  // interface and is recorded in docs/ROADMAP.md.
  const [wanted, setWanted] = useState(INITIAL_PAGE);

  const load = useCallback(async () => {
    if (!provider?.connected) return;

    setLoading(true);
    setError(null);

    try {
      const inbox = await api.listEmailMessages(userId, { limit: wanted });
      setRows(inbox.items);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The mailbox could not be read.",
      );
    } finally {
      setLoading(false);
    }
  }, [provider?.connected, userId, wanted]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!provider?.connected) {
    return (
      <EmptyState
        title="No mailbox is connected"
        description="Triage reads real messages from a connected mailbox. Nothing is shown here until one is configured on the server — no sample inbox, and no placeholder mail. Composing, templates and drafts all work in the meantime."
      />
    );
  }

  async function triage(row: TriagedMessage) {
    setBusy(row.message.message_id);
    setError(null);

    try {
      const assessment = await api.triageEmail(userId, {
        message_id: row.message.message_id,
      });

      setRows((current) =>
        current.map((item) =>
          item.message.message_id === row.message.message_id
            ? { ...item, assessment }
            : item,
        ),
      );
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "That message could not be assessed.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function draftReply(row: TriagedMessage) {
    setBusy(row.message.message_id);
    setError(null);

    try {
      const full =
        row.message.body === null
          ? (await api.getEmailMessage(userId, row.message.message_id)).message
          : row.message;

      const generated = await api.composeEmail(userId, {
        operation: "reply",
        instruction: "Write a reply to this message.",
        source_subject: full.subject,
        source_body: full.body ?? full.snippet,
        source_sender: full.sender?.address ?? null,
        use_knowledge_base: true,
      });

      const draft = await api.createEmailDraft(userId, {
        to_recipients: full.sender ? [full.sender.address] : [],
        subject: generated.subject || `Re: ${full.subject}`,
        body: generated.body,
        in_reply_to_message_id: full.message_id,
        provider_thread_id: full.thread_id,
        generated_by_ai: true,
      });

      onReply(draft);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The reply could not be drafted.",
      );
    } finally {
      setBusy(null);
    }
  }

  const groups = groupByCategory(rows, (row) => row.assessment?.category ?? null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink-900">
          Inbox <span className="font-normal text-ink-500">({rows.length})</span>
        </h2>
        <Button type="button" onClick={() => void load()} disabled={loading}>
          Refresh
        </Button>
      </div>

      {error ? <ErrorNotice message={error} onRetry={() => void load()} /> : null}

      {loading ? (
        <div className="py-10 text-center">
          <Spinner label="Reading the mailbox" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="Nothing in the mailbox"
          description="The mailbox is connected and returned no messages."
        />
      ) : (
        groups.map((group) => (
          <div key={group.label}>
            <p className="mb-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
              {group.label}
            </p>
            <ul className="space-y-2">
              {group.items.map((row) => (
                <MessageRow
                  key={row.message.message_id}
                  row={row}
                  busy={busy === row.message.message_id}
                  onTriage={() => void triage(row)}
                  onReply={() => void draftReply(row)}
                />
              ))}
            </ul>
          </div>
        ))
      )}

      {/* Offered only while there is reason to think there is more. A full
          page means the mailbox had at least as many as were asked for; a
          short one means the end has been reached, and a button that fetches
          the same list again would be a button that does nothing. */}
      {!loading && rows.length >= wanted && wanted < MAX_PAGE ? (
        <div className="pt-1 text-center">
          <Button
            type="button"
            onClick={() => setWanted((current) => Math.min(current + PAGE_STEP, MAX_PAGE))}
          >
            Load more
          </Button>
          <p className="mt-1 text-xs text-ink-500">
            Showing the {rows.length} most recent messages.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function MessageRow({
  row,
  busy,
  onTriage,
  onReply,
}: {
  row: TriagedMessage;
  busy: boolean;
  onTriage: () => void;
  onReply: () => void;
}) {
  const { message, assessment } = row;

  return (
    <li className="rounded-lg border border-ink-200 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink-900">
            {message.subject || "(no subject)"}
          </p>
          <p className="truncate text-xs text-ink-500">
            {message.sender?.name ?? message.sender?.address ?? "Unknown sender"}
            {message.received_at ? ` · ${formatDateTime(message.received_at)}` : ""}
            {message.attachments.length > 0
              ? ` · ${message.attachments.length} attachment${message.attachments.length === 1 ? "" : "s"}`
              : ""}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {assessment ? (
            <>
              <Badge tone={describeCategory(assessment.category).tone}>
                {describeCategory(assessment.category).label}
              </Badge>
              <Badge tone={describePriority(assessment.priority).tone}>
                {describePriority(assessment.priority).label}
              </Badge>
            </>
          ) : (
            <Badge tone="neutral">Not yet triaged</Badge>
          )}
        </div>
      </div>

      {assessment ? (
        <div className="mt-2 space-y-1.5">
          <p className="text-sm text-ink-700">{assessment.summary}</p>
          {assessment.suggested_action ? (
            <p className="text-xs text-ink-600">
              <span className="font-medium">Suggested:</span>{" "}
              {assessment.suggested_action}
            </p>
          ) : null}
          {assessment.action_items.length > 0 ? (
            <ul className="space-y-0.5">
              {assessment.action_items.map((item) => (
                <li key={item} className="text-xs text-ink-600">
                  · {item}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <p className="mt-2 text-sm text-ink-500">{message.snippet}</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button type="button" onClick={onTriage} disabled={busy}>
          {busy ? "Working…" : assessment ? "Re-assess" : "Triage"}
        </Button>
        <Button type="button" variant="ghost" onClick={onReply} disabled={busy}>
          Draft a reply
        </Button>
      </div>
    </li>
  );
}
