import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { Mailbox } from "../../api/types";
import { useTwins } from "../../state/TwinContext";
import { Badge, Button, ErrorNotice, Spinner } from "../ui";

/**
 * Connecting the mailbox the selected Digital Twin's Email Agent acts on.
 *
 * Two things this screen deliberately does not do.
 *
 * **It never asks for a user id.** Whose mailbox this is comes from the twin
 * already selected in the sidebar, and the server takes it from the identity
 * header rather than from the request body. A UUID field here would be both a
 * usability failure and a door onto everybody else's mail.
 *
 * **It never reports a connection the server did not confirm.** The address is
 * shown as connected only after the server has stored it, and "stored" is
 * still weaker than "reachable" — the provider status strip beside it is what
 * says whether Graph actually answers. Those are separate facts and they fail
 * separately: a mistyped address is a correction, and a missing Mail.Read
 * consent is a job for a tenant administrator.
 *
 * A person with no mailbox is in a supported state, not a broken one, and the
 * copy says so: composing, rewriting, templates and saved drafts all work
 * without one.
 */
export function MailboxPanel({ onChanged }: { onChanged?: () => void }) {
  const { currentTwin } = useTwins();
  const userId = currentTwin?.id ?? null;

  const [mailbox, setMailbox] = useState<Mailbox | null>(null);
  const [address, setAddress] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!userId) {
      setMailbox(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    // The reported symptom: switching to Robert briefly showed Sudha's
    // address. Clearing first means the panel shows "checking" rather than
    // somebody else's mailbox.
    setMailbox(null);
    setAddress("");
    setNotice(null);

    try {
      const found = await api.getMailbox(userId);
      setMailbox(found);
      setAddress(found.address ?? "");
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Unable to check which mailbox is connected.",
      );
    } finally {
      setLoading(false);
    }
  }, [userId]);

  // Keyed on the twin: switching person must re-read the mailbox rather than
  // leaving the previous one's address on screen.
  useEffect(() => {
    void load();
  }, [load]);

  async function connect() {
    if (!userId || !address.trim()) return;

    setSaving(true);
    setError(null);
    setNotice(null);

    try {
      const saved = await api.setMailbox(userId, { address: address.trim() });
      setMailbox(saved);
      setNotice(`Mailbox set to ${saved.address}.`);
      onChanged?.();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The mailbox was not connected.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function disconnect() {
    if (!userId) return;

    setSaving(true);
    setError(null);
    setNotice(null);

    try {
      const cleared = await api.disconnectMailbox(userId);
      setMailbox(cleared);
      setAddress("");
      setNotice("Mailbox disconnected.");
      onChanged?.();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The mailbox was not disconnected.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!userId) {
    return (
      <p className="text-sm text-ink-500">
        Select a Digital Twin to connect a mailbox for.
      </p>
    );
  }

  if (loading) {
    return <Spinner label="Checking the mailbox" />;
  }

  return (
    <div className="rounded-md border border-ink-200 bg-white px-4 py-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink-800">
          Mailbox for {currentTwin?.name}
        </h3>
        {mailbox?.connected ? (
          <Badge tone={mailbox.shared_fallback ? "warning" : "positive"}>
            {mailbox.shared_fallback ? "Shared fallback" : "Connected"}
          </Badge>
        ) : (
          <Badge tone="neutral">Not connected</Badge>
        )}
      </div>

      {mailbox?.connected ? (
        <p className="mt-1 text-sm text-ink-600">
          Reading and sending as{" "}
          <span className="font-medium text-ink-900">{mailbox.address}</span>
          {mailbox.provider ? ` via ${mailbox.provider}` : null}.
        </p>
      ) : (
        <p className="mt-1 text-sm leading-relaxed text-ink-600">
          Composing, rewriting, templates and saved drafts all work without a
          mailbox. Listing an inbox, sending, and the email digests do not.
        </p>
      )}

      {mailbox?.shared_fallback ? (
        <p className="mt-1.5 text-sm text-amber-700">
          This address comes from the server's fallback setting rather than from
          this person. Connect an individual mailbox to keep one person's mail
          out of another's workspace.
        </p>
      ) : null}

      {mailbox?.detail && !mailbox.connected ? (
        <p className="mt-1.5 text-sm text-ink-500">{mailbox.detail}</p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <div className="min-w-[16rem] flex-1">
          <label
            htmlFor="mailbox-address"
            className="block text-xs font-medium text-ink-600"
          >
            Mailbox address
          </label>
          <input
            id="mailbox-address"
            type="email"
            autoComplete="off"
            placeholder="person@sunradia.com"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
          />
        </div>

        <Button
          type="button"
          variant="primary"
          disabled={saving || !address.trim() || address.trim() === mailbox?.address}
          onClick={() => void connect()}
        >
          {saving ? "Saving…" : mailbox?.connected ? "Change" : "Connect"}
        </Button>

        {mailbox?.connected && !mailbox.shared_fallback ? (
          <Button type="button" variant="danger" disabled={saving} onClick={() => void disconnect()}>
            Disconnect
          </Button>
        ) : null}
      </div>

      {notice ? <p className="mt-2 text-sm text-emerald-700">{notice}</p> : null}
      {error ? (
        <div className="mt-2">
          <ErrorNotice message={error} />
        </div>
      ) : null}
    </div>
  );
}
