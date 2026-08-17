/**
 * Conversation history, held in the browser.
 *
 * The backend's chat endpoint is stateless — it stores no history and
 * consults none — so this is explicitly a UI convenience, not a mirror of
 * anything server-side. Every answer is still produced from the documents
 * alone. Keeping the store here, as pure functions over a plain array, is
 * what lets the rules below be tested without a browser.
 */

import type { ChatSource } from "../api/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: ChatSource[];
  retrievedChunks?: number;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  /** Which Digital Twin answered, so a reopened chat says who it was. */
  twinId: string | null;
  createdAt: number;
  updatedAt: number;
}

export const UNTITLED = "New conversation";

export function createConversation(twinId: string | null, now: number): Conversation {
  return {
    id: `chat-${now}-${Math.random().toString(36).slice(2, 8)}`,
    title: UNTITLED,
    messages: [],
    twinId,
    createdAt: now,
    updatedAt: now,
  };
}

export function isEmpty(conversation: Conversation | undefined): boolean {
  return !conversation || conversation.messages.length === 0;
}

/**
 * "New conversation" when one is already open and empty.
 *
 * Pressing it repeatedly used to stack identical empty entries in the
 * sidebar, each looking like a real chat. An empty conversation is already
 * a new conversation, so the correct response is to keep it — retargeted at
 * the currently selected twin — rather than to add another.
 */
export function startConversation(
  conversations: Conversation[],
  activeId: string | null,
  twinId: string | null,
  now: number,
): { conversations: Conversation[]; activeId: string } {
  const active = conversations.find((item) => item.id === activeId);

  if (active && isEmpty(active)) {
    return {
      conversations: conversations.map((item) =>
        item.id === active.id ? { ...item, twinId, updatedAt: now } : item,
      ),
      activeId: active.id,
    };
  }

  // Any other empty conversation lying around is reused too, so the list
  // cannot accumulate them by switching away and back.
  const strayEmpty = conversations.find(isEmpty);

  if (strayEmpty) {
    return {
      conversations: conversations.map((item) =>
        item.id === strayEmpty.id ? { ...item, twinId, updatedAt: now } : item,
      ),
      activeId: strayEmpty.id,
    };
  }

  const created = createConversation(twinId, now);

  return { conversations: [created, ...conversations], activeId: created.id };
}

/** The first question names the chat, so history is scannable. */
export function titleFor(messages: ChatMessage[]): string {
  const first = messages.find((message) => message.role === "user");

  if (!first) return UNTITLED;

  const trimmed = first.text.trim().replace(/\s+/g, " ");

  return trimmed.length > 48 ? `${trimmed.slice(0, 48)}…` : trimmed || UNTITLED;
}

export function withMessages(
  conversations: Conversation[],
  id: string,
  messages: ChatMessage[],
  now: number,
): Conversation[] {
  return conversations.map((conversation) =>
    conversation.id === id
      ? {
          ...conversation,
          messages,
          // A renamed chat keeps its name; only an untitled one is named by
          // its first question.
          title:
            conversation.title === UNTITLED ? titleFor(messages) : conversation.title,
          updatedAt: now,
        }
      : conversation,
  );
}

export function renameConversation(
  conversations: Conversation[],
  id: string,
  title: string,
  now: number,
): Conversation[] {
  const cleaned = title.trim();

  return conversations.map((conversation) =>
    conversation.id === id
      ? { ...conversation, title: cleaned || UNTITLED, updatedAt: now }
      : conversation,
  );
}

/**
 * Delete, and make sure something is still selected afterwards.
 *
 * Deleting the last conversation leaves a fresh empty one rather than an
 * empty workspace with no way back into chat.
 */
export function deleteConversation(
  conversations: Conversation[],
  id: string,
  activeId: string | null,
  twinId: string | null,
  now: number,
): { conversations: Conversation[]; activeId: string } {
  const remaining = conversations.filter((conversation) => conversation.id !== id);

  if (remaining.length === 0) {
    const created = createConversation(twinId, now);

    return { conversations: [created], activeId: created.id };
  }

  return {
    conversations: remaining,
    activeId: activeId === id ? remaining[0].id : (activeId ?? remaining[0].id),
  };
}

export type ConversationGroup = "Today" | "Yesterday" | "Older";

export function groupFor(timestamp: number, now: number): ConversationGroup {
  const startOfToday = new Date(now).setHours(0, 0, 0, 0);
  const startOfYesterday = startOfToday - 86_400_000;

  if (timestamp >= startOfToday) return "Today";
  if (timestamp >= startOfYesterday) return "Yesterday";

  return "Older";
}

export function groupConversations(
  conversations: Conversation[],
  now: number,
): Array<{ group: ConversationGroup; conversations: Conversation[] }> {
  const order: ConversationGroup[] = ["Today", "Yesterday", "Older"];
  const buckets = new Map<ConversationGroup, Conversation[]>();

  for (const conversation of [...conversations].sort(
    (a, b) => b.updatedAt - a.updatedAt,
  )) {
    const group = groupFor(conversation.updatedAt, now);
    buckets.set(group, [...(buckets.get(group) ?? []), conversation]);
  }

  return order
    .filter((group) => (buckets.get(group)?.length ?? 0) > 0)
    .map((group) => ({ group, conversations: buckets.get(group) ?? [] }));
}

// --- persistence ---------------------------------------------------------

const STORAGE_KEY = "sunradia.conversations.v1";

export function serialise(conversations: Conversation[]): string {
  return JSON.stringify(conversations);
}

/** Tolerant on purpose: a corrupt or outdated blob starts fresh. */
export function deserialise(raw: string | null): Conversation[] {
  if (!raw) return [];

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed.filter(
      (item): item is Conversation =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as Conversation).id === "string" &&
        Array.isArray((item as Conversation).messages),
    );
  } catch {
    return [];
  }
}

export const CONVERSATION_STORAGE_KEY = STORAGE_KEY;
