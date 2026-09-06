import { useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { User, UserDeletionPreview } from "../../api/types";
import { describeOwnedData } from "../../lib/twin";
import { Button, ErrorNotice, Spinner } from "../ui";

/**
 * The confirmation before a Digital Twin and everything it owns is destroyed.
 *
 * The dialog asks the server what would be deleted *before* asking the person
 * to confirm, and shows the counts. "This will delete 14 drafts and 60
 * memories" is a decision somebody can actually make; "this cannot be undone"
 * is a sentence they click past. It also states what is **not** deleted — the
 * shared knowledge base is company-wide, is not owned by this person, and
 * survives — because the most likely reason to hesitate is fear of taking the
 * corpus with them.
 *
 * Typing the name to confirm is deliberate friction. The action is
 * irreversible and unlogged elsewhere, and a single click is not enough
 * ceremony for that.
 */
export function DeleteTwinDialog({
  twin,
  adminId,
  onCancel,
  onDeleted,
}: {
  twin: User;
  /** Who is doing the deleting. The server requires an administrator, and the
   * two identities are different people — hence a separate parameter rather
   * than the ambient current twin. */
  adminId: string;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const [preview, setPreview] = useState<UserDeletionPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmation, setConfirmation] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);

      try {
        const found = await api.previewUserDeletion(adminId, twin.id);
        if (!cancelled) setPreview(found);
      } catch (cause) {
        if (!cancelled) {
          setError(
            cause instanceof ApiError
              ? cause.message
              : "Unable to check what would be deleted.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [adminId, twin.id]);

  async function remove() {
    setDeleting(true);
    setError(null);

    try {
      await api.deleteUser(adminId, twin.id);
      onDeleted();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The Digital Twin was not deleted.",
      );
      setDeleting(false);
    }
  }

  // Nothing is confirmable until the preview has loaded: agreeing to delete
  // an unknown quantity of data is not informed consent.
  const armed = preview !== null && confirmation.trim() === twin.name.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 px-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-twin-title"
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-ink-200 bg-white p-5 shadow-lg"
      >
        <h2 id="delete-twin-title" className="text-base font-semibold text-ink-900">
          Delete {twin.name}?
        </h2>
        <p className="mt-1 text-sm text-ink-600">
          This permanently deletes this Digital Twin and everything it owns. It cannot
          be undone.
        </p>

        {loading ? (
          <div className="py-6 text-center">
            <Spinner label="Checking what would be deleted" />
          </div>
        ) : error && !preview ? (
          <div className="mt-3">
            <ErrorNotice message={error} />
          </div>
        ) : preview ? (
          <>
            <div className="mt-4 rounded-md border border-rose-200 bg-rose-50/60 px-4 py-3">
              <p className="text-sm font-medium text-rose-800">
                Will be permanently deleted
              </p>
              <ul className="mt-1.5 space-y-0.5 text-sm text-rose-900">
                {describeOwnedData(preview.owned).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              {preview.owned_total === 0 ? (
                <p className="mt-1 text-sm text-rose-900">
                  This Digital Twin owns no data yet.
                </p>
              ) : null}
            </div>

            <div className="mt-3 rounded-md border border-ink-200 px-4 py-3">
              <p className="text-sm font-medium text-ink-800">Will be kept</p>
              <p className="mt-1 text-sm text-ink-600">
                {preview.shared_knowledge_note}
              </p>
              <p className="mt-1 text-sm text-ink-800">
                {preview.shared_knowledge_documents} shared Knowledge Base document
                {preview.shared_knowledge_documents === 1 ? "" : "s"}, and every
                OneDrive source.
              </p>
            </div>

            <label htmlFor="delete-confirm" className="mt-4 block text-sm text-ink-700">
              Type <span className="font-medium text-ink-900">{twin.name}</span> to
              confirm
            </label>
            <input
              id="delete-confirm"
              type="text"
              value={confirmation}
              autoComplete="off"
              onChange={(event) => setConfirmation(event.target.value)}
              className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            />

            {error ? (
              <div className="mt-3">
                <ErrorNotice message={error} />
              </div>
            ) : null}
          </>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" onClick={onCancel} disabled={deleting}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={() => void remove()}
            disabled={!armed || deleting}
          >
            {deleting ? "Deleting…" : "Delete permanently"}
          </Button>
        </div>
      </div>
    </div>
  );
}
