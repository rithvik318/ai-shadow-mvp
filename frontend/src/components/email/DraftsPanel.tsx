import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { EmailDraft, EmailDraftStatus } from "../../api/types";
import { describeDraftStatus, isEditable } from "../../lib/email";
import { formatDateTime } from "../../lib/format";
import { Badge, Button, EmptyState, ErrorNotice, Spinner } from "../ui";

/**
 * Saved drafts, including sent ones.
 *
 * Sent drafts stay in the list so the workspace can show what actually went
 * out. They cannot be edited — the message is gone, and rewriting the record
 * of it would make the record wrong about what somebody received. They can be
 * deleted, which forgets this system's copy and unsends nothing.
 */
interface Props {
  userId: string;
  reloadToken: number;
  onEdit: (draft: EmailDraft) => void;
}

const FILTERS: Array<{ label: string; value: EmailDraftStatus | "all" }> = [
  { label: "All", value: "all" },
  { label: "Needs review", value: "needs_review" },
  { label: "Ready to send", value: "approved" },
  { label: "Sent", value: "sent" },
  { label: "Failed", value: "failed" },
];

export function DraftsPanel({ userId, reloadToken, onEdit }: Props) {
  const [drafts, setDrafts] = useState<EmailDraft[]>([]);
  const [filter, setFilter] = useState<EmailDraftStatus | "all">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const list = await api.listEmailDrafts(userId);
      setDrafts(list.items);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Drafts could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
  }, [load, reloadToken]);

  async function remove(draft: EmailDraft) {
    const warning =
      draft.status === "sent"
        ? "Delete the record of this sent email? It does not unsend anything."
        : "Delete this draft?";

    if (!window.confirm(warning)) return;

    try {
      await api.deleteEmailDraft(userId, draft.id);
      setDrafts((current) => current.filter((item) => item.id !== draft.id));
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "That draft could not be deleted.",
      );
    }
  }

  const visible =
    filter === "all" ? drafts : drafts.filter((draft) => draft.status === filter);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink-900">
          Drafts <span className="font-normal text-ink-500">({visible.length})</span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={filter === item.value}
              onClick={() => setFilter(item.value)}
              className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                filter === item.value
                  ? "bg-ink-900 text-white"
                  : "text-ink-600 hover:bg-ink-100"
              }`}
            >
              {item.label}
            </button>
          ))}
          <Button type="button" onClick={() => void load()} disabled={loading}>
            Refresh
          </Button>
        </div>
      </div>

      {error ? <ErrorNotice message={error} onRetry={() => void load()} /> : null}

      {loading ? (
        <div className="py-10 text-center">
          <Spinner label="Loading drafts" />
        </div>
      ) : drafts.length === 0 ? (
        <EmptyState
          title="No drafts yet"
          description="Anything you write or generate in Compose and save appears here, including emails that have been sent."
        />
      ) : visible.length === 0 ? (
        <p className="rounded-md border border-dashed border-ink-300 px-4 py-8 text-center text-sm text-ink-500">
          No drafts with that status.
        </p>
      ) : (
        <ul className="space-y-2">
          {visible.map((draft) => {
            const status = describeDraftStatus(draft.status);

            return (
              <li key={draft.id} className="rounded-lg border border-ink-200 px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink-900">
                      {draft.subject || "(no subject)"}
                    </p>
                    <p className="truncate text-xs text-ink-500">
                      {draft.to_recipients.length > 0
                        ? draft.to_recipients.join(", ")
                        : "No recipients yet"}
                      {" · "}
                      {formatDateTime(draft.updated_at)}
                      {draft.attachments.length > 0
                        ? ` · ${draft.attachments.length} attachment${draft.attachments.length === 1 ? "" : "s"}`
                        : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                    {draft.generated_by_ai ? (
                      <Badge tone="info">AI generated</Badge>
                    ) : null}
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                </div>

                <p className="mt-1.5 line-clamp-2 text-sm text-ink-600">{draft.body}</p>
                <p className="mt-1.5 text-xs text-ink-400">{status.hint}</p>

                {draft.send_error ? (
                  <p className="mt-1.5 text-xs text-rose-700">
                    The mailbox refused this: {draft.send_error}
                  </p>
                ) : null}

                <div className="mt-3 flex flex-wrap gap-2">
                  {isEditable(draft) ? (
                    <Button type="button" onClick={() => onEdit(draft)}>
                      {draft.status === "needs_review" ? "Review" : "Continue editing"}
                    </Button>
                  ) : (
                    <Button type="button" variant="ghost" onClick={() => onEdit(draft)}>
                      View
                    </Button>
                  )}
                  <Button type="button" variant="danger" onClick={() => void remove(draft)}>
                    Delete
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
