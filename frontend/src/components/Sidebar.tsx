import { type ChangeEvent } from "react";

import { groupConversations, type Conversation } from "../lib/conversations";
import { useTwins } from "../state/TwinContext";
import { Button } from "./ui";

export type Section =
  | "chat"
  | "twins"
  | "knowledge"
  | "email"
  | "tasks"
  | "report";

const SECTIONS: Array<{ id: Section; label: string; hint: string }> = [
  { id: "chat", label: "Chat", hint: "Ask your Digital Twin" },
  { id: "twins", label: "Digital Twins", hint: "Profiles, preferences, memory" },
  { id: "knowledge", label: "Knowledge Base", hint: "Documents and sync" },
  { id: "email", label: "Email Agent", hint: "Draft, triage, follow up" },
  { id: "tasks", label: "Tasks", hint: "What you need to do" },
  { id: "report", label: "Reports", hint: "Work report and email digests" },
];

interface Props {
  section: Section;
  onSection: (section: Section) => void;
  conversations: Conversation[];
  activeId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
  open: boolean;
  onClose: () => void;
}

export function Sidebar({
  section,
  onSection,
  conversations,
  activeId,
  onSelectConversation,
  onNewConversation,
  onRename,
  onDelete,
  open,
  onClose,
}: Props) {
  const { twins, currentTwin, select, loading } = useTwins();
  const groups = groupConversations(conversations, Date.now());

  return (
    <>
      {open ? (
        <div
          className="fixed inset-0 z-10 bg-ink-900/40 lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      ) : null}

      <nav
        aria-label="Main"
        className={`fixed inset-y-0 left-0 z-20 flex w-[17rem] flex-col border-r border-ink-200 bg-white transition-transform lg:static lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="border-b border-ink-200 px-5 py-4">
          <p className="text-[0.8125rem] font-bold uppercase tracking-[0.14em] text-accent-700">
            SunRadia
          </p>
          <p className="mt-0.5 text-xs text-ink-500">Digital Twin Platform</p>
        </div>

        <div className="px-3 py-3">
          <p className="px-2 pb-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
            Workspace
          </p>
          <ul className="space-y-0.5">
            {SECTIONS.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  aria-current={section === item.id ? "page" : undefined}
                  onClick={() => {
                    onSection(item.id);
                    onClose();
                  }}
                  className={`w-full rounded-md px-2.5 py-2 text-left transition-colors ${
                    section === item.id
                      ? "bg-accent-50 text-accent-700"
                      : "text-ink-700 hover:bg-ink-50"
                  }`}
                >
                  <span className="block text-sm font-medium">{item.label}</span>
                  <span className="block text-xs text-ink-500">{item.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="border-y border-ink-200 px-5 py-3">
          <p className="text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
            Current Digital Twin
          </p>
          {loading ? (
            <p className="mt-1.5 text-sm text-ink-500">Loading…</p>
          ) : twins.length === 0 ? (
            <p className="mt-1.5 text-xs leading-snug text-ink-500">
              None yet. Add one in Digital Twins.
            </p>
          ) : (
            <>
              <label htmlFor="twin-select" className="sr-only">
                Select the active Digital Twin
              </label>
              <select
                id="twin-select"
                value={currentTwin?.id ?? ""}
                onChange={(event: ChangeEvent<HTMLSelectElement>) =>
                  select(event.target.value)
                }
                className="mt-1.5 w-full rounded-md border border-ink-200 bg-white px-2.5 py-1.5 text-sm font-medium text-ink-900"
              >
                {twins.map((twin) => (
                  <option key={twin.id} value={twin.id}>
                    {twin.name} · {twin.role}
                  </option>
                ))}
              </select>
            </>
          )}
          <p className="mt-1.5 text-[0.6875rem] leading-snug text-ink-400">
            Identity only, not sign-in. Sets whose profile and memory shape answers.
          </p>
        </div>

        {section === "chat" ? (
          <>
            <div className="px-3 py-3">
              <Button
                type="button"
                variant="primary"
                className="w-full"
                onClick={() => {
                  onNewConversation();
                  onClose();
                }}
              >
                New conversation
              </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
              {conversations.length === 0
                ? null
                : groups.map(({ group, conversations: inGroup }) => (
                    <div key={group} className="mb-3">
                      <p className="px-2 pb-1 text-[0.6875rem] font-semibold uppercase tracking-wider text-ink-400">
                        {group}
                      </p>
                      <ul className="space-y-0.5">
                        {inGroup.map((conversation) => (
                          <li key={conversation.id} className="group relative">
                            <button
                              type="button"
                              aria-current={
                                conversation.id === activeId ? "page" : undefined
                              }
                              onClick={() => {
                                onSelectConversation(conversation.id);
                                onClose();
                              }}
                              className={`w-full truncate rounded-md py-1.5 pl-2.5 pr-14 text-left text-sm ${
                                conversation.id === activeId
                                  ? "bg-ink-100 font-medium text-ink-900"
                                  : "text-ink-600 hover:bg-ink-50"
                              }`}
                            >
                              {conversation.title}
                            </button>
                            <span className="absolute right-1 top-1 hidden gap-0.5 group-hover:flex">
                              <IconAction
                                label={`Rename ${conversation.title}`}
                                onClick={() => onRename(conversation.id)}
                              >
                                Rename
                              </IconAction>
                              <IconAction
                                label={`Delete ${conversation.title}`}
                                onClick={() => onDelete(conversation.id)}
                              >
                                Delete
                              </IconAction>
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
            </div>
          </>
        ) : (
          <div className="flex-1" />
        )}
      </nav>
    </>
  );
}

function IconAction({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={children}
      onClick={onClick}
      className="rounded px-1.5 py-0.5 text-[0.6875rem] text-ink-500 hover:bg-ink-200 hover:text-ink-800"
    >
      {children}
    </button>
  );
}
