import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { EmailAssessment, EmailDraft } from "../../api/types";
import { describePriority, partitionFollowUps } from "../../lib/email";
import { formatDateTime } from "../../lib/format";
import { Badge, Button, EmptyState, ErrorNotice, Spinner } from "../ui";

/**
 * Follow-ups triage recommended.
 *
 * Recommendations, never actions. Nothing here is scheduled, nothing is sent,
 * and nothing is marked handled on anybody's behalf — "Mark handled" is a
 * button a person presses.
 *
 * A due date appears only when the message itself gave one. The backend never
 * chooses a deadline, so an undated follow-up is outstanding rather than
 * overdue, and the two are shown separately.
 */
interface Props {
  userId: string;
  onDraftReply: (draft: EmailDraft) => void;
}

export function FollowUpsPanel({ userId, onDraftReply }: Props) {
  const [items, setItems] = useState<EmailAssessment[]>([]);
  const [includeHandled, setIncludeHandled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const list = await api.listEmailFollowUps(userId, { includeHandled });
      setItems(list.items);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Follow-ups could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [includeHandled, userId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function setHandled(assessment: EmailAssessment, handled: boolean) {
    setBusy(assessment.id);

    try {
      await api.setEmailFollowUpHandled(userId, assessment.id, handled);
      await load();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "That could not be updated.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function draftFollowUp(assessment: EmailAssessment) {
    setBusy(assessment.id);
    setError(null);

    try {
      const generated = await api.composeEmail(userId, {
        operation: "generate",
        instruction:
          `Write a short follow-up about "${assessment.subject ?? "our last exchange"}". ` +
          `Context from the original message: ${assessment.summary}` +
          (assessment.follow_up_reason
            ? ` Reason to follow up: ${assessment.follow_up_reason}`
            : ""),
        use_knowledge_base: true,
      });

      const draft = await api.createEmailDraft(userId, {
        to_recipients: assessment.sender ? [assessment.sender] : [],
        subject: generated.subject || `Following up: ${assessment.subject ?? ""}`,
        body: generated.body,
        in_reply_to_message_id: assessment.provider_message_id,
        provider_thread_id: assessment.provider_thread_id,
        generated_by_ai: true,
      });

      onDraftReply(draft);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "The follow-up could not be drafted.",
      );
    } finally {
      setBusy(null);
    }
  }

  const { overdue, upcoming } = partitionFollowUps(items, Date.now());

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">
            Follow-ups <span className="font-normal text-ink-500">({items.length})</span>
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            Suggestions from triage. Nothing is sent or scheduled automatically.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-ink-600">
            <input
              type="checkbox"
              checked={includeHandled}
              onChange={() => setIncludeHandled((current) => !current)}
            />
            Show handled
          </label>
          <Button type="button" onClick={() => void load()} disabled={loading}>
            Refresh
          </Button>
        </div>
      </div>

      {error ? <ErrorNotice message={error} onRetry={() => void load()} /> : null}

      {loading ? (
        <div className="py-10 text-center">
          <Spinner label="Loading follow-ups" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="Nothing to follow up"
          description="Follow-ups appear once messages have been triaged. Triage runs on real mail from a connected mailbox, or on a message you supply — nothing is invented."
        />
      ) : (
        <>
          {overdue.length > 0 ? (
            <Group
              title="Overdue"
              items={overdue}
              busy={busy}
              onHandled={setHandled}
              onDraft={draftFollowUp}
            />
          ) : null}
          {upcoming.length > 0 ? (
            <Group
              title={overdue.length > 0 ? "Outstanding" : "Follow-ups"}
              items={upcoming}
              busy={busy}
              onHandled={setHandled}
              onDraft={draftFollowUp}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

function Group({
  title,
  items,
  busy,
  onHandled,
  onDraft,
}: {
  title: string;
  items: EmailAssessment[];
  busy: string | null;
  onHandled: (assessment: EmailAssessment, handled: boolean) => void;
  onDraft: (assessment: EmailAssessment) => void;
}) {
  return (
    <div>
      <p className="mb-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
        {title}
      </p>
      <ul className="space-y-2">
        {items.map((assessment) => (
          <li key={assessment.id} className="rounded-lg border border-ink-200 px-4 py-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink-900">
                  {assessment.subject || "(no subject)"}
                </p>
                <p className="truncate text-xs text-ink-500">
                  {assessment.sender ?? "Unknown sender"}
                  {assessment.follow_up_due_at
                    ? ` · due ${formatDateTime(assessment.follow_up_due_at)}`
                    : " · no date given"}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {assessment.handled ? <Badge tone="neutral">Handled</Badge> : null}
                <Badge tone={describePriority(assessment.priority).tone}>
                  {describePriority(assessment.priority).label}
                </Badge>
              </div>
            </div>

            <p className="mt-1.5 text-sm text-ink-700">{assessment.summary}</p>

            {assessment.follow_up_reason ? (
              <p className="mt-1 text-xs text-ink-600">
                <span className="font-medium">Why:</span> {assessment.follow_up_reason}
              </p>
            ) : null}

            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                type="button"
                onClick={() => onDraft(assessment)}
                disabled={busy === assessment.id}
              >
                {busy === assessment.id ? "Working…" : "Draft a follow-up"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onHandled(assessment, !assessment.handled)}
                disabled={busy === assessment.id}
              >
                {assessment.handled ? "Reopen" : "Mark handled"}
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
