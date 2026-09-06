/**
 * What "preferences" means here, stated once.
 *
 * The backend has no preferences table and no preferences endpoint. What it
 * has is two things, and this module is the single place that says so:
 *
 *   1. `communication_style` and `decision_preferences` on the profile
 *   2. memories whose `type` is `preference`
 *
 * The UI presents those together under one heading. Nothing is invented, and
 * no field is shown that `ProfileRequest` would reject.
 */

import type { Memory, Profile } from "../api/types";

export function preferenceMemories(memories: Memory[]): Memory[] {
  return memories.filter((memory) => memory.type === "preference");
}

/** How many preferences a twin holds, across both places they live. */
export function countPreferences(profile: Profile | null, memories: Memory[]): number {
  const fromProfile =
    (profile?.decision_preferences.length ?? 0) +
    (profile?.communication_style ? 1 : 0);

  return fromProfile + preferenceMemories(memories).length;
}

/**
 * Has a memory passed its expiry?
 *
 * Retrieval already excludes expired memories from the prompt, so this is
 * only about telling the user what is and is not currently shaping answers.
 */
export function isExpired(memory: Memory, now: number): boolean {
  if (!memory.expires_at) return false;

  const expiry = new Date(memory.expires_at).getTime();

  return Number.isFinite(expiry) && expiry <= now;
}

/** Exactly the memories the backend would put in front of the model. */
export function activeMemories(memories: Memory[], now: number): Memory[] {
  return memories.filter((memory) => memory.active && !isExpired(memory, now));
}

export function memoryStateLabel(
  memory: Memory,
  now: number,
): { label: string; tone: "positive" | "neutral" | "warning" } {
  if (!memory.active) return { label: "Retired", tone: "neutral" };
  if (isExpired(memory, now)) return { label: "Expired", tone: "warning" };

  return { label: "Active", tone: "positive" };
}

/** Only the fields `ProfileRequest` accepts. `bio` is not one of them. */
export const EDITABLE_PROFILE_TEXT = [
  { key: "name", label: "Name" },
  { key: "role", label: "Role" },
  { key: "organization", label: "Organization" },
] as const;

export const EDITABLE_PROFILE_LISTS = [
  { key: "responsibilities", label: "Responsibilities" },
  { key: "expertise", label: "Expertise" },
  { key: "priorities", label: "Priorities" },
  { key: "current_focus", label: "Current focus" },
] as const;

export function parseList(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/**
 * The per-table counts of a deletion preview, as sentences.
 *
 * A table name is not a warning. `email_assessment: 60` tells somebody nothing
 * about what they are about to lose, and the dialog exists to make an
 * irreversible act informed — so each table is named in the words the product
 * uses for it, and tables with nothing in them are left out rather than
 * padding the list with zeroes.
 *
 * A table this map does not know is still shown, humanised from its name,
 * because silently omitting user data from a deletion warning is the one
 * failure this function must not have.
 */
const OWNED_LABELS: Record<string, [string, string]> = {
  task: ["task", "tasks"],
  generated_report: ["stored report", "stored reports"],
  calendar_event: ["meeting", "meetings"],
  email_assessment: ["triaged message", "triaged messages"],
  email_draft: ["email draft", "email drafts"],
  email_attachment: ["attachment", "attachments"],
  email_template: ["email template", "email templates"],
  user_mailbox: ["mailbox configuration", "mailbox configurations"],
  digital_twin_memory: ["memory", "memories"],
  digital_twin_profile: ["profile", "profiles"],
};

export function describeOwnedData(owned: Record<string, number>): string[] {
  return Object.entries(owned)
    .filter(([, count]) => count > 0)
    .map(([table, count]) => {
      const [singular, plural] = OWNED_LABELS[table] ?? [
        table.replace(/_/g, " "),
        `${table.replace(/_/g, " ")} rows`,
      ];

      return `${count} ${count === 1 ? singular : plural}`;
    })
    .sort();
}
