import { useCallback, useEffect, useState } from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { EmailDraft, EmailProviderStatus } from "../../api/types";
import { describeProvider } from "../../lib/email";
import { useTwins } from "../../state/TwinContext";
import { Badge, EmptyState, ErrorNotice } from "../ui";
import { ComposePanel } from "./ComposePanel";
import { DraftsPanel } from "./DraftsPanel";
import { FollowUpsPanel } from "./FollowUpsPanel";
import { InboxPanel } from "./InboxPanel";
import { TemplatesPanel } from "./TemplatesPanel";

/**
 * The Email Agent workspace.
 *
 * Owns three things and delegates everything else: which tab is open, the
 * provider status (loaded once and passed down, because five panels each
 * polling it would be five requests for one answer), and the draft currently
 * open in the composer — which is what lets Drafts and Follow-ups hand work to
 * Compose without a router.
 *
 * The provider banner is deliberately prominent and deliberately honest. When
 * no mailbox is connected it says so and says what still works, rather than
 * showing an empty inbox that looks like a quiet morning.
 */
type Tab = "inbox" | "compose" | "templates" | "drafts" | "follow-ups";

const TABS: Array<{ id: Tab; label: string; needsMailbox: boolean }> = [
  { id: "inbox", label: "Inbox", needsMailbox: true },
  { id: "compose", label: "Compose", needsMailbox: false },
  { id: "templates", label: "Templates", needsMailbox: false },
  { id: "drafts", label: "Drafts", needsMailbox: false },
  { id: "follow-ups", label: "Follow-ups", needsMailbox: false },
];

export function EmailPanel({ onOpenTasks }: { onOpenTasks: () => void }) {
  const { currentTwin } = useTwins();
  const [tab, setTab] = useState<Tab>("compose");
  const [provider, setProvider] = useState<EmailProviderStatus | null>(null);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [openDraft, setOpenDraft] = useState<EmailDraft | null>(null);
  const [draftsToken, setDraftsToken] = useState(0);

  // The mailbox is per-user, so the answer depends on who is selected — and
  // on the first render nobody is, because `TwinProvider` is still loading the
  // list. Keying this on the twin's id is what makes the call wait for one and
  // re-ask when it changes; with an empty dependency list it fired once,
  // before any identity existed, and came back 401.
  const twinId = currentTwin?.id ?? null;

  const loadProvider = useCallback(async () => {
    if (!twinId) return;

    // Cleared before the new answer arrives, not after. Leaving the previous
    // person's status in place meant the Inbox tab rendered against *their*
    // connection while this person's request was still in flight — showing a
    // connected mailbox to somebody who has none.
    setProvider(null);
    setProviderError(null);

    try {
      setProvider(await api.getEmailProviderStatus(twinId));
    } catch (cause) {
      setProviderError(
        cause instanceof ApiError
          ? cause.message
          : "Unable to reach the backend to check the mailbox connection.",
      );
    }
  }, [twinId]);

  useEffect(() => {
    void loadProvider();
  }, [loadProvider]);

  const editDraft = useCallback((draft: EmailDraft) => {
    setOpenDraft(draft);
    setTab("compose");
  }, []);

  const onDraftChanged = useCallback(() => {
    setDraftsToken((token) => token + 1);
  }, []);

  if (!currentTwin) {
    return (
      <section className="flex h-full min-w-0 flex-col bg-white">
        <Header />
        <div className="flex-1 px-6 py-10">
          <EmptyState
            title="Choose a Digital Twin first"
            description="Email is written as somebody. Select or create a Digital Twin — its role, priorities and communication style are what shape a draft."
          />
        </div>
      </section>
    );
  }

  const presentation = describeProvider(provider);

  return (
    <section className="flex h-full min-w-0 flex-col bg-white">
      <Header />

      <div className="border-b border-ink-200 px-6">
        <div className="flex flex-wrap items-center gap-1 pb-2">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={tab === item.id ? "page" : undefined}
              onClick={() => setTab(item.id)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                tab === item.id
                  ? "bg-accent-50 text-accent-700"
                  : "text-ink-600 hover:bg-ink-50"
              }`}
            >
              {item.label}
              {item.needsMailbox && !provider?.connected ? (
                <span className="ml-1.5 text-[0.6875rem] text-ink-400">
                  needs mailbox
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {providerError ? (
          <div className="mb-4">
            <ErrorNotice message={providerError} onRetry={() => void loadProvider()} />
          </div>
        ) : (
          <div className="mb-5 rounded-lg border border-ink-200 px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-ink-800">Mailbox</span>
              <Badge tone={presentation.tone}>{presentation.label}</Badge>
              {provider?.mailbox ? (
                <span className="text-xs text-ink-500">{provider.mailbox}</span>
              ) : null}
            </div>
            <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-ink-600">
              {provider?.connected
                ? "Messages below come from the connected mailbox. Sending still requires you to approve each draft."
                : (provider?.detail ??
                  "No mailbox is connected yet.")}
            </p>
            {!provider?.connected ? (
              <p className="mt-1.5 text-xs text-ink-400">
                Composing, rewriting, templates and saved drafts all work without a
                mailbox. Nothing can be sent until one is connected on the server,
                and no message is ever shown that a mailbox did not supply.
              </p>
            ) : null}
          </div>
        )}

        {tab === "inbox" ? (
          <InboxPanel
            userId={currentTwin.id}
            provider={provider}
            onReply={editDraft}
          />
        ) : tab === "compose" ? (
          <ComposePanel
            userId={currentTwin.id}
            twinName={currentTwin.name}
            twinRole={currentTwin.role}
            provider={provider}
            draft={openDraft}
            onDraftChanged={onDraftChanged}
            onClearDraft={() => setOpenDraft(null)}
          />
        ) : tab === "templates" ? (
          <TemplatesPanel userId={currentTwin.id} onUseTemplate={editDraft} />
        ) : tab === "drafts" ? (
          <DraftsPanel
            userId={currentTwin.id}
            reloadToken={draftsToken}
            onEdit={editDraft}
          />
        ) : (
          <FollowUpsPanel
            userId={currentTwin.id}
            onDraftReply={editDraft}
            onOpenTasks={onOpenTasks}
          />
        )}
      </div>
    </section>
  );
}

function Header() {
  return (
    <header className="border-b border-ink-200 px-6 py-4">
      <h1 className="text-base font-semibold text-ink-900">Email Agent</h1>
      <p className="mt-0.5 text-xs text-ink-500">
        Drafts written as your Digital Twin, from the shared company knowledge base.
        Every send is approved by you.
      </p>
    </header>
  );
}
