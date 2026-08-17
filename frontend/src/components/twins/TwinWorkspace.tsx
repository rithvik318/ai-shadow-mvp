import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { Memory, MemoryType, Profile, ProfileUpdate, User } from "../../api/types";
import { MEMORY_TYPES } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import {
  EDITABLE_PROFILE_LISTS,
  EDITABLE_PROFILE_TEXT,
  activeMemories,
  memoryStateLabel,
  parseList,
  preferenceMemories,
} from "../../lib/twin";
import { Badge, Button, ErrorNotice, Spinner } from "../ui";

type Tab = "overview" | "profile" | "preferences" | "memories";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "profile", label: "Profile" },
  { id: "preferences", label: "Preferences" },
  { id: "memories", label: "Memories" },
];

interface Props {
  twin: User;
  isActive: boolean;
  onMakeActive: () => void;
  onBack: () => void;
}

export function TwinWorkspace({ twin, isActive, onMakeActive, onBack }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    const [profileResult, memoryResult] = await Promise.allSettled([
      api.getProfile(twin.id),
      api.listMemories(twin.id),
    ]);

    if (profileResult.status === "fulfilled") {
      setProfile(profileResult.value);
    } else {
      setProfile(null);
      // No profile yet is an empty state, not a failure.
      if (
        !(profileResult.reason instanceof ApiError && profileResult.reason.isNotFound)
      ) {
        setError("Unable to load this Digital Twin's profile. Try again.");
      }
    }

    if (memoryResult.status === "fulfilled") {
      setMemories(memoryResult.value.items);
    } else {
      setMemories([]);
      setError("Unable to load memories. Try again.");
    }

    setLoading(false);
  }, [twin.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const now = Date.now();

  return (
    <section className="flex h-full min-w-0 flex-col bg-white">
      <header className="border-b border-ink-200 px-6 pt-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <button
              type="button"
              onClick={onBack}
              className="text-xs text-ink-500 hover:text-ink-800"
            >
              ← All Digital Twins
            </button>
            <h1 className="mt-1 truncate text-base font-semibold text-ink-900">
              {twin.name} · {twin.role}
            </h1>
            <p className="mt-0.5 truncate text-xs text-ink-500">{twin.email}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {isActive ? (
              <Badge tone="info">Active twin</Badge>
            ) : (
              <Button type="button" onClick={onMakeActive}>
                Make active
              </Button>
            )}
          </div>
        </div>

        <nav aria-label="Digital Twin sections" className="-mb-px mt-3 flex gap-4">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={tab === item.id ? "page" : undefined}
              onClick={() => setTab(item.id)}
              className={`border-b-2 px-0.5 pb-2 text-sm transition-colors ${
                tab === item.id
                  ? "border-accent-600 font-medium text-ink-900"
                  : "border-transparent text-ink-500 hover:text-ink-800"
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {error ? <ErrorNotice message={error} onRetry={() => void load()} /> : null}

        {loading ? (
          <Spinner label="Loading Digital Twin…" />
        ) : tab === "overview" ? (
          <Overview profile={profile} memories={memories} now={now} />
        ) : tab === "profile" ? (
          <ProfileForm twin={twin} profile={profile} onSaved={setProfile} />
        ) : tab === "preferences" ? (
          <Preferences
            twin={twin}
            profile={profile}
            memories={memories}
            onSaved={setProfile}
          />
        ) : (
          <Memories
            twin={twin}
            memories={memories}
            onMemories={setMemories}
            now={now}
          />
        )}
      </div>
    </section>
  );
}

function Overview({
  profile,
  memories,
  now,
}: {
  profile: Profile | null;
  memories: Memory[];
  now: number;
}) {
  const active = activeMemories(memories, now);

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Memories" value={memories.length} />
        <Stat label="Shaping answers now" value={active.length} />
        <Stat label="Preference memories" value={preferenceMemories(memories).length} />
      </div>

      <div className="rounded-lg border border-ink-200 px-4 py-3.5">
        <h2 className="text-sm font-semibold text-ink-900">How this twin is used</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-ink-600">
          When a question is asked as this twin, its profile and active memories are
          placed above the retrieved documents to shape tone, emphasis and priorities.
          They are never cited as sources — only documents are. Retired and expired
          memories are excluded.
        </p>
      </div>

      {profile ? null : (
        <p className="rounded-md border border-dashed border-ink-300 px-4 py-3 text-sm text-ink-500">
          No profile yet. Answers still work; they are simply written without a persona.
          Add one under Profile.
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-ink-200 px-4 py-3">
      <p className="text-xl font-semibold tabular-nums text-ink-900">{value}</p>
      <p className="mt-0.5 text-xs text-ink-500">{label}</p>
    </div>
  );
}

/** Every field here exists on `ProfileRequest`. There is no `bio`. */
function ProfileForm({
  twin,
  profile,
  onSaved,
}: {
  twin: User;
  profile: Profile | null;
  onSaved: (profile: Profile) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => ({
    name: profile?.name ?? twin.name,
    role: profile?.role ?? twin.role,
    organization: profile?.organization ?? "",
    communication_style: profile?.communication_style ?? "",
    responsibilities: (profile?.responsibilities ?? []).join("\n"),
    expertise: (profile?.expertise ?? []).join("\n"),
    priorities: (profile?.priorities ?? []).join("\n"),
    current_focus: (profile?.current_focus ?? []).join("\n"),
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function set(key: string) {
    return (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const next = event.target.value;
      setValues((current) => ({ ...current, [key]: next }));
      setSaved(false);
    };
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);

    const body: ProfileUpdate = {
      name: values.name.trim(),
      role: values.role.trim(),
      organization: values.organization.trim(),
      communication_style: values.communication_style.trim(),
      responsibilities: parseList(values.responsibilities),
      expertise: parseList(values.expertise),
      priorities: parseList(values.priorities),
      current_focus: parseList(values.current_focus),
    };

    try {
      onSaved(await api.saveProfile(twin.id, body));
      setSaved(true);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Could not save the profile.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-3xl space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        {EDITABLE_PROFILE_TEXT.map((field) => (
          <TextField
            key={field.key}
            label={field.label}
            value={values[field.key]}
            onChange={set(field.key)}
          />
        ))}
      </div>

      <ListField
        label="Communication style"
        hint="One line. Shapes how answers are written."
        value={values.communication_style}
        onChange={set("communication_style")}
        rows={2}
      />

      {EDITABLE_PROFILE_LISTS.map((field) => (
        <ListField
          key={field.key}
          label={field.label}
          hint="One per line."
          value={values[field.key]}
          onChange={set(field.key)}
        />
      ))}

      {error ? <ErrorNotice message={error} /> : null}

      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
        {saved ? (
          <span className="text-sm text-emerald-700">Profile saved.</span>
        ) : null}
      </div>
    </form>
  );
}

/**
 * Preferences, meaning the two things the backend actually stores: the
 * profile's communication style and decision preferences, and memories typed
 * `preference`. There is no preferences endpoint and none is implied.
 */
function Preferences({
  twin,
  profile,
  memories,
  onSaved,
}: {
  twin: User;
  profile: Profile | null;
  memories: Memory[];
  onSaved: (profile: Profile) => void;
}) {
  const [style, setStyle] = useState(profile?.communication_style ?? "");
  const [decisions, setDecisions] = useState(
    (profile?.decision_preferences ?? []).join("\n"),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const asMemories = preferenceMemories(memories);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);

    try {
      onSaved(
        await api.saveProfile(twin.id, {
          communication_style: style.trim(),
          decision_preferences: parseList(decisions),
        }),
      );
      setSaved(true);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Could not save preferences.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <form onSubmit={save} className="space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">Response preferences</h2>
          <p className="mt-0.5 text-xs text-ink-500">
            Stored on the twin&rsquo;s profile and applied to every answer it gives.
          </p>
        </div>

        <ListField
          label="Communication style"
          hint="How answers should read. One line."
          value={style}
          onChange={(event) => {
            setStyle(event.target.value);
            setSaved(false);
          }}
          rows={2}
        />
        <ListField
          label="Decision preferences"
          hint="One per line. Constrains what the twin recommends."
          value={decisions}
          onChange={(event) => {
            setDecisions(event.target.value);
            setSaved(false);
          }}
        />

        {error ? <ErrorNotice message={error} /> : null}

        <div className="flex items-center gap-3">
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? "Saving…" : "Save preferences"}
          </Button>
          {saved ? (
            <span className="text-sm text-emerald-700">Preferences saved.</span>
          ) : null}
        </div>
      </form>

      <div>
        <h2 className="text-sm font-semibold text-ink-900">
          Preference memories ({asMemories.length})
        </h2>
        <p className="mt-0.5 text-xs text-ink-500">
          Memories typed <code className="text-[0.6875rem]">preference</code>. Managed
          under Memories, and shown here because they shape answers the same way.
        </p>
        {asMemories.length === 0 ? (
          <p className="mt-2 rounded-md border border-dashed border-ink-300 px-4 py-3 text-sm text-ink-500">
            No preference memories yet.
          </p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {asMemories.map((memory) => (
              <li
                key={memory.id}
                className="rounded-md border border-ink-200 px-3.5 py-2.5 text-sm text-ink-800"
              >
                {memory.content}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Memories({
  twin,
  memories,
  onMemories,
  now,
}: {
  twin: User;
  memories: Memory[];
  onMemories: (memories: Memory[]) => void;
  now: number;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({
    content: "",
    type: "fact" as MemoryType,
    importance: 3,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  async function add(event: FormEvent) {
    event.preventDefault();
    setBusy("add");
    setError(null);

    try {
      const created = await api.createMemory(twin.id, {
        type: draft.type,
        content: draft.content.trim(),
        importance: draft.importance,
      });
      onMemories([created, ...memories]);
      setDraft({ content: "", type: "fact", importance: 3 });
      setAdding(false);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not add the memory.");
    } finally {
      setBusy(null);
    }
  }

  async function saveEdit(memory: Memory) {
    setBusy(memory.id);
    setError(null);

    try {
      const updated = await api.updateMemory(twin.id, memory.id, {
        content: editText.trim(),
      });
      onMemories(memories.map((item) => (item.id === memory.id ? updated : item)));
      setEditing(null);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Could not save the memory.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function remove(memory: Memory) {
    if (!window.confirm(`Delete this memory?\n\n${memory.content}`)) return;

    setBusy(memory.id);
    setError(null);

    try {
      await api.deleteMemory(twin.id, memory.id);
      onMemories(memories.filter((item) => item.id !== memory.id));
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Could not delete the memory.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-600">
          Memories are what this twin durably knows. Active ones are placed above the
          retrieved documents in every answer.
        </p>
        <Button type="button" variant="primary" onClick={() => setAdding((v) => !v)}>
          {adding ? "Cancel" : "Add Memory"}
        </Button>
      </div>

      {error ? <ErrorNotice message={error} /> : null}

      {adding ? (
        <form
          onSubmit={add}
          className="rounded-lg border border-ink-200 bg-ink-50/60 p-4"
        >
          <ListField
            label="Memory"
            hint="One durable thing worth remembering."
            value={draft.content}
            onChange={(event) =>
              setDraft((d) => ({ ...d, content: event.target.value }))
            }
            rows={3}
          />
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <label
                htmlFor="memory-type"
                className="block text-xs font-medium text-ink-600"
              >
                Type
              </label>
              <select
                id="memory-type"
                value={draft.type}
                onChange={(event: ChangeEvent<HTMLSelectElement>) =>
                  setDraft((d) => ({ ...d, type: event.target.value as MemoryType }))
                }
                className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
              >
                {MEMORY_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="memory-importance"
                className="block text-xs font-medium text-ink-600"
              >
                Importance (1–5)
              </label>
              <input
                id="memory-importance"
                type="number"
                min={1}
                max={5}
                value={draft.importance}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  setDraft((d) => ({ ...d, importance: Number(event.target.value) }))
                }
                className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
              />
            </div>
          </div>
          <div className="mt-3">
            <Button
              type="submit"
              variant="primary"
              disabled={busy === "add" || !draft.content.trim()}
            >
              {busy === "add" ? "Adding…" : "Add memory"}
            </Button>
          </div>
        </form>
      ) : null}

      {memories.length === 0 ? (
        <p className="rounded-md border border-dashed border-ink-300 px-4 py-6 text-center text-sm text-ink-500">
          No memories have been added yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {memories.map((memory) => {
            const state = memoryStateLabel(memory, now);

            return (
              <li
                key={memory.id}
                className={`rounded-lg border px-4 py-3 ${
                  state.label === "Active"
                    ? "border-ink-200"
                    : "border-ink-200 bg-ink-50/60"
                }`}
              >
                {editing === memory.id ? (
                  <div className="space-y-2">
                    <textarea
                      value={editText}
                      rows={3}
                      onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
                        setEditText(event.target.value)
                      }
                      className="w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
                    />
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="primary"
                        onClick={() => void saveEdit(memory)}
                        disabled={busy === memory.id || !editText.trim()}
                      >
                        Save
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => setEditing(null)}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <p
                        className={`text-sm leading-relaxed ${
                          state.label === "Active" ? "text-ink-800" : "text-ink-500"
                        }`}
                      >
                        {memory.content}
                      </p>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Badge>{memory.type}</Badge>
                        <Badge tone={state.tone}>{state.label}</Badge>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-ink-500">
                        Importance {memory.importance} · added{" "}
                        {formatDateTime(memory.created_at)}
                        {memory.expires_at
                          ? ` · expires ${formatDateTime(memory.expires_at)}`
                          : ""}
                      </p>
                      <div className="flex gap-1.5">
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => {
                            setEditing(memory.id);
                            setEditText(memory.content);
                          }}
                        >
                          Edit
                        </Button>
                        <Button
                          type="button"
                          variant="danger"
                          onClick={() => void remove(memory)}
                          disabled={busy === memory.id}
                        >
                          Delete
                        </Button>
                      </div>
                    </div>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  // The union, because one handler is shared with the textarea fields below.
  onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
}) {
  const id = `profile-${label.toLowerCase().replace(/\s+/g, "-")}`;

  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-ink-600">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={onChange}
        className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
      />
    </div>
  );
}

function ListField({
  label,
  hint,
  value,
  onChange,
  rows = 4,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  rows?: number;
}) {
  const id = `list-${label.toLowerCase().replace(/\s+/g, "-")}`;

  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-ink-600">
        {label}
      </label>
      {hint ? <p className="text-[0.6875rem] text-ink-400">{hint}</p> : null}
      <textarea
        id={id}
        rows={rows}
        value={value}
        onChange={onChange}
        className="mt-1 w-full resize-y rounded-md border border-ink-200 px-2.5 py-1.5 text-sm leading-relaxed"
      />
    </div>
  );
}
