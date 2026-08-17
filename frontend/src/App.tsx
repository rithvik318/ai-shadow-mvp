import { useCallback, useEffect, useState } from "react";

import { ChatPanel } from "./components/chat/ChatPanel";
import { KnowledgePanel } from "./components/knowledge/KnowledgePanel";
import { Sidebar, type Section } from "./components/Sidebar";
import { TwinsPanel } from "./components/twins/TwinsPanel";
import {
  CONVERSATION_STORAGE_KEY,
  createConversation,
  deleteConversation as removeConversation,
  deserialise,
  renameConversation,
  serialise,
  startConversation,
  withMessages,
  type ChatMessage,
  type Conversation,
} from "./lib/conversations";
import { TwinProvider, useTwins } from "./state/TwinContext";

export default function App() {
  return (
    <TwinProvider>
      <Workspace />
    </TwinProvider>
  );
}

function Workspace() {
  const { currentTwin } = useTwins();
  const [section, setSection] = useState<Section>("chat");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  // Restored from the browser, because the backend's chat endpoint is
  // stateless and stores no history. This is a UI convenience and is
  // described as one; it is not a mirror of anything server-side.
  const [conversations, setConversations] = useState<Conversation[]>(() => {
    const restored = deserialise(window.localStorage.getItem(CONVERSATION_STORAGE_KEY));

    return restored.length > 0 ? restored : [createConversation(null, Date.now())];
  });
  const [activeId, setActiveId] = useState<string>(() => conversations[0].id);

  useEffect(() => {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, serialise(conversations));
  }, [conversations]);

  const active =
    conversations.find((conversation) => conversation.id === activeId) ??
    conversations[0];

  const onMessages = useCallback(
    (messages: ChatMessage[]) => {
      setConversations((current) =>
        withMessages(current, active.id, messages, Date.now()),
      );
    },
    [active.id],
  );

  const onNewConversation = useCallback(() => {
    setConversations((current) => {
      const result = startConversation(
        current,
        activeId,
        currentTwin?.id ?? null,
        Date.now(),
      );
      setActiveId(result.activeId);

      return result.conversations;
    });
    setSection("chat");
  }, [activeId, currentTwin]);

  const onRename = useCallback(
    (id: string) => {
      const conversation = conversations.find((item) => item.id === id);
      const next = window.prompt("Rename conversation", conversation?.title ?? "");

      if (next === null) return;

      setConversations((current) => renameConversation(current, id, next, Date.now()));
    },
    [conversations],
  );

  const onDelete = useCallback(
    (id: string) => {
      const conversation = conversations.find((item) => item.id === id);

      // Only confirm when there is something to lose.
      if (
        conversation &&
        conversation.messages.length > 0 &&
        !window.confirm(`Delete "${conversation.title}"? This cannot be undone.`)
      ) {
        return;
      }

      setConversations((current) => {
        const result = removeConversation(
          current,
          id,
          activeId,
          currentTwin?.id ?? null,
          Date.now(),
        );
        setActiveId(result.activeId);

        return result.conversations;
      });
    },
    [activeId, conversations, currentTwin],
  );

  return (
    <div className="flex h-full">
      <Sidebar
        section={section}
        onSection={setSection}
        conversations={conversations}
        activeId={active.id}
        onSelectConversation={setActiveId}
        onNewConversation={onNewConversation}
        onRename={onRename}
        onDelete={onDelete}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <button
          type="button"
          onClick={() => setSidebarOpen(true)}
          className="border-b border-ink-200 bg-white px-4 py-2.5 text-left text-sm font-medium text-ink-700 lg:hidden"
        >
          ☰ Menu
        </button>

        <main className="min-h-0 flex-1">
          {section === "chat" ? (
            <ChatPanel
              key={active.id}
              conversation={active}
              onMessages={onMessages}
              onIngested={() => setReloadToken((token) => token + 1)}
              onNewConversation={onNewConversation}
            />
          ) : section === "twins" ? (
            <TwinsPanel />
          ) : (
            <KnowledgePanel reloadToken={reloadToken} />
          )}
        </main>
      </div>
    </div>
  );
}
