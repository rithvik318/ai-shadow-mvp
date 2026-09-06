/**
 * Digital Twin context: what counts as a preference, and which memories are
 * actually shaping answers.
 *
 * The first suite is the important one. The backend has no preferences
 * table and no preferences endpoint, so "preferences" has to mean something
 * precise or the UI is inventing a feature.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  EDITABLE_PROFILE_LISTS,
  EDITABLE_PROFILE_TEXT,
  activeMemories,
  countPreferences,
  describeOwnedData,
  isExpired,
  memoryStateLabel,
  parseList,
  preferenceMemories,
} from "../lib/twin.ts";
import type { Memory, MemoryType, Profile } from "../api/types.ts";

const NOW = new Date("2026-08-17T12:00:00Z").getTime();

function memory(overrides: Partial<Memory> = {}): Memory {
  return {
    id: overrides.id ?? "m1",
    type: (overrides.type ?? "fact") as MemoryType,
    content: overrides.content ?? "Something remembered.",
    importance: overrides.importance ?? 3,
    source: "user",
    active: overrides.active ?? true,
    expires_at: overrides.expires_at ?? null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

function profile(overrides: Partial<Profile> = {}): Profile {
  return {
    id: "p1",
    name: "Sudha",
    role: "CEO",
    organization: "SunRadia",
    communication_style: overrides.communication_style ?? null,
    responsibilities: [],
    expertise: [],
    priorities: [],
    decision_preferences: overrides.decision_preferences ?? [],
    current_focus: [],
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

describe("preferences are the two things the backend actually has", () => {
  it("counts preference-typed memories", () => {
    const memories = [memory({ type: "preference" }), memory({ type: "fact" })];

    assert.equal(preferenceMemories(memories).length, 1);
  });

  it("counts the profile's decision preferences", () => {
    assert.equal(
      countPreferences(profile({ decision_preferences: ["a", "b"] }), []),
      2,
    );
  });

  it("counts communication style as one preference", () => {
    assert.equal(countPreferences(profile({ communication_style: "Concise" }), []), 1);
  });

  it("adds both places together", () => {
    const count = countPreferences(
      profile({ decision_preferences: ["a"], communication_style: "Concise" }),
      [memory({ type: "preference" }), memory({ type: "decision" })],
    );

    assert.equal(count, 3);
  });

  it("is zero with no profile and no memories, not an error", () => {
    assert.equal(countPreferences(null, []), 0);
  });

  it("exposes only profile fields the backend accepts", () => {
    // ProfileRequest has no `bio`. Showing one would be a field the API
    // silently drops, and a user editing it would see their work vanish.
    const keys = [
      ...EDITABLE_PROFILE_TEXT.map((f) => f.key),
      ...EDITABLE_PROFILE_LISTS.map((f) => f.key),
    ];

    assert.deepEqual([...keys].sort(), [
      "current_focus",
      "expertise",
      "name",
      "organization",
      "priorities",
      "responsibilities",
      "role",
    ]);
    assert.ok(!keys.includes("bio" as never));
  });
});

describe("memory state", () => {
  it("treats a past expiry as expired", () => {
    assert.equal(isExpired(memory({ expires_at: "2026-01-01T00:00:00Z" }), NOW), true);
  });

  it("treats a future expiry as still active", () => {
    assert.equal(isExpired(memory({ expires_at: "2027-01-01T00:00:00Z" }), NOW), false);
  });

  it("treats no expiry as never expiring", () => {
    assert.equal(isExpired(memory({ expires_at: null }), NOW), false);
  });

  it("excludes retired and expired memories from what shapes an answer", () => {
    const memories = [
      memory({ id: "a" }),
      memory({ id: "b", active: false }),
      memory({ id: "c", expires_at: "2026-01-01T00:00:00Z" }),
    ];

    assert.deepEqual(
      activeMemories(memories, NOW).map((m) => m.id),
      ["a"],
    );
  });

  it("labels the three states distinctly", () => {
    assert.equal(memoryStateLabel(memory(), NOW).label, "Active");
    assert.equal(memoryStateLabel(memory({ active: false }), NOW).label, "Retired");
    assert.equal(
      memoryStateLabel(memory({ expires_at: "2026-01-01T00:00:00Z" }), NOW).label,
      "Expired",
    );
  });

  it("does not mark an expired memory as a failure", () => {
    assert.notEqual(
      memoryStateLabel(memory({ expires_at: "2026-01-01T00:00:00Z" }), NOW).tone,
      "positive",
    );
  });
});

describe("list editing", () => {
  it("splits a textarea into entries and drops blank lines", () => {
    assert.deepEqual(parseList("One\n\n  Two  \nThree\n"), ["One", "Two", "Three"]);
  });

  it("returns nothing for an empty field", () => {
    assert.deepEqual(parseList("   "), []);
  });
});

describe("what a deletion would destroy", () => {
  it("names each table in the words the product uses", () => {
    // A table name is not a warning. `email_assessment: 60` tells somebody
    // nothing about what they are about to lose.
    assert.deepEqual(
      describeOwnedData({
        task: 3,
        digital_twin_memory: 60,
        email_draft: 1,
      }),
      ["1 email draft", "3 tasks", "60 memories"],
    );
  });

  it("leaves out tables with nothing in them", () => {
    assert.deepEqual(describeOwnedData({ task: 0, email_draft: 2 }), [
      "2 email drafts",
    ]);
  });

  it("uses the singular for one row", () => {
    assert.deepEqual(describeOwnedData({ digital_twin_memory: 1 }), ["1 memory"]);
    assert.deepEqual(describeOwnedData({ calendar_event: 1 }), ["1 meeting"]);
  });

  it("still shows a table it has never heard of", () => {
    // Silently omitting user data from a deletion warning is the one failure
    // this function must not have.
    assert.deepEqual(describeOwnedData({ future_table: 2 }), ["2 future table rows"]);
  });

  it("says nothing at all for a twin that owns nothing", () => {
    assert.deepEqual(describeOwnedData({}), []);
    assert.deepEqual(describeOwnedData({ task: 0 }), []);
  });
});
