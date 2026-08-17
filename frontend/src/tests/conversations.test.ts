/**
 * Conversation history. The first two suites exist because of a reported
 * bug: pressing "New conversation" repeatedly stacked identical empty
 * entries in the sidebar, each looking like a real chat.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  UNTITLED,
  createConversation,
  deleteConversation,
  deserialise,
  groupConversations,
  groupFor,
  renameConversation,
  serialise,
  startConversation,
  titleFor,
  withMessages,
  type ChatMessage,
  type Conversation,
} from "../lib/conversations.ts";

const NOW = new Date("2026-08-17T12:00:00Z").getTime();

function said(text: string): ChatMessage {
  return { id: "m1", role: "user", text };
}

function withOne(now = NOW): { list: Conversation[]; id: string } {
  const created = createConversation("twin-a", now);

  return { list: [created], id: created.id };
}

describe("new conversation", () => {
  it("reuses the open conversation when it is already empty", () => {
    const { list, id } = withOne();

    const result = startConversation(list, id, "twin-a", NOW);

    assert.equal(result.conversations.length, 1);
    assert.equal(result.activeId, id);
  });

  it("does not stack empty conversations however often it is pressed", () => {
    const state = withOne();
    let conversations = state.list;
    let activeId: string = state.id;

    for (let press = 0; press < 5; press += 1) {
      const result = startConversation(conversations, activeId, "twin-a", NOW);
      conversations = result.conversations;
      activeId = result.activeId;
    }

    assert.equal(conversations.length, 1);
    assert.equal(conversations.filter((c) => c.messages.length === 0).length, 1);
  });

  it("creates one once the open conversation has content", () => {
    const { list, id } = withOne();
    const used = withMessages(list, id, [said("Who are our clients?")], NOW);

    const result = startConversation(used, id, "twin-a", NOW);

    assert.equal(result.conversations.length, 2);
    assert.notEqual(result.activeId, id);
  });

  it("reuses a stray empty conversation rather than adding another", () => {
    // Reachable by starting a chat, switching to an older one, and pressing
    // New again.
    const first = createConversation("twin-a", NOW);
    const second = { ...createConversation("twin-a", NOW + 1), messages: [said("hi")] };

    const result = startConversation([second, first], second.id, "twin-a", NOW);

    assert.equal(result.conversations.length, 2);
    assert.equal(result.activeId, first.id);
  });

  it("retargets the reused conversation at the selected twin", () => {
    const { list, id } = withOne();

    const result = startConversation(list, id, "twin-b", NOW);

    assert.equal(result.conversations[0].twinId, "twin-b");
  });
});

describe("titles", () => {
  it("names a conversation after its first question", () => {
    assert.equal(
      titleFor([said("What did we propose to Freddie Mac?")]),
      "What did we propose to Freddie Mac?",
    );
  });

  it("truncates a long question rather than breaking the sidebar", () => {
    const title = titleFor([said("x".repeat(200))]);

    assert.ok(title.length <= 49);
    assert.match(title, /…$/);
  });

  it("stays untitled until something is asked", () => {
    assert.equal(titleFor([]), UNTITLED);
  });

  it("does not rename a conversation the user has already named", () => {
    const { list, id } = withOne();
    const named = renameConversation(list, id, "Freddie Mac work", NOW);

    const after = withMessages(named, id, [said("Something else entirely")], NOW);

    assert.equal(after[0].title, "Freddie Mac work");
  });
});

describe("rename", () => {
  it("renames the conversation", () => {
    const { list, id } = withOne();

    assert.equal(
      renameConversation(list, id, "Q3 proposals", NOW)[0].title,
      "Q3 proposals",
    );
  });

  it("falls back to the placeholder for a blank name", () => {
    const { list, id } = withOne();

    assert.equal(renameConversation(list, id, "   ", NOW)[0].title, UNTITLED);
  });
});

describe("delete", () => {
  it("removes the conversation", () => {
    const first = createConversation("t", NOW);
    const second = { ...createConversation("t", NOW + 1), messages: [said("hi")] };

    const result = deleteConversation([first, second], second.id, second.id, "t", NOW);

    assert.equal(result.conversations.length, 1);
    assert.equal(result.conversations[0].id, first.id);
  });

  it("selects something else when the active one is deleted", () => {
    const first = { ...createConversation("t", NOW), messages: [said("a")] };
    const second = { ...createConversation("t", NOW + 1), messages: [said("b")] };

    const result = deleteConversation([first, second], first.id, first.id, "t", NOW);

    assert.equal(result.activeId, second.id);
  });

  it("leaves a fresh conversation rather than an empty workspace", () => {
    const { list, id } = withOne();

    const result = deleteConversation(list, id, id, "t", NOW);

    assert.equal(result.conversations.length, 1);
    assert.equal(result.conversations[0].messages.length, 0);
    assert.equal(result.activeId, result.conversations[0].id);
  });
});

describe("grouping", () => {
  it("buckets by day", () => {
    assert.equal(groupFor(NOW, NOW), "Today");
    assert.equal(groupFor(NOW - 86_400_000, NOW), "Yesterday");
    assert.equal(groupFor(NOW - 5 * 86_400_000, NOW), "Older");
  });

  it("orders groups and puts the most recent chat first", () => {
    const today = { ...createConversation("t", NOW), updatedAt: NOW };
    const older = { ...createConversation("t", NOW), updatedAt: NOW - 5 * 86_400_000 };

    const grouped = groupConversations([older, today], NOW);

    assert.deepEqual(
      grouped.map((g) => g.group),
      ["Today", "Older"],
    );
  });

  it("omits groups with nothing in them", () => {
    const grouped = groupConversations(
      [{ ...createConversation("t", NOW), updatedAt: NOW }],
      NOW,
    );

    assert.equal(grouped.length, 1);
  });
});

describe("persistence", () => {
  it("survives a round trip", () => {
    const { list } = withOne();

    assert.equal(deserialise(serialise(list)).length, 1);
  });

  it("starts fresh rather than throwing on a corrupt store", () => {
    assert.deepEqual(deserialise("not json"), []);
    assert.deepEqual(deserialise(null), []);
    assert.deepEqual(deserialise('{"not":"an array"}'), []);
  });

  it("drops entries that are not conversations", () => {
    assert.deepEqual(deserialise('[{"id":"x"},{"nope":1}]'), []);
  });
});
