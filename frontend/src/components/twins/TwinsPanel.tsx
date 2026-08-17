import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type { Profile, User } from "../../api/types";
import { countPreferences } from "../../lib/twin";
import { useTwins } from "../../state/TwinContext";
import { Button, EmptyState, ErrorNotice, Spinner } from "../ui";
import { TwinWorkspace } from "./TwinWorkspace";

interface TwinCounts {
  memories: number;
  preferences: number;
}

export function TwinsPanel() {
  const { twins, currentTwin, loading, error, select, refresh, create } = useTwins();
  const [openTwin, setOpenTwin] = useState<User | null>(null);
  const [adding, setAdding] = useState(false);
  const [counts, setCounts] = useState<Record<string, TwinCounts>>({});

  /**
   * One scoped pair of calls per twin. There is no aggregate endpoint, so the
   * alternative is showing no counts at all — and a failure for one twin must
   * not blank the others, hence the per-twin catch.
   */
  const loadCounts = useCallback(async (list: User[]) => {
    const results = await Promise.all(
      list.map(async (twin) => {
        try {
          const [memories, profile] = await Promise.all([
            api.listMemories(twin.id),
            api.getProfile(twin.id).catch((cause: unknown) => {
              if (cause instanceof ApiError && cause.isNotFound) return null;
              throw cause;
            }),
          ]);

          return [
            twin.id,
            {
              memories: memories.total,
              preferences: countPreferences(profile as Profile | null, memories.items),
            },
          ] as const;
        } catch {
          return [twin.id, { memories: 0, preferences: 0 }] as const;
        }
      }),
    );

    setCounts(Object.fromEntries(results));
  }, []);

  useEffect(() => {
    if (twins.length > 0) void loadCounts(twins);
  }, [twins, loadCounts]);

  if (openTwin) {
    return (
      <TwinWorkspace
        twin={openTwin}
        isActive={openTwin.id === currentTwin?.id}
        onMakeActive={() => select(openTwin.id)}
        onBack={() => {
          setOpenTwin(null);
          void loadCounts(twins);
        }}
      />
    );
  }

  return (
    <section className="flex h-full min-w-0 flex-col bg-white">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-200 px-6 py-4">
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-ink-900">Digital Twins</h1>
          <p className="mt-0.5 text-xs text-ink-500">
            Each twin has its own profile, preferences and memory. The knowledge base is
            shared by all of them.
          </p>
        </div>
        <Button type="button" variant="primary" onClick={() => setAdding(true)}>
          Add Digital Twin
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {error ? <ErrorNotice message={error} onRetry={() => void refresh()} /> : null}

        {adding ? (
          <AddTwinForm
            onCancel={() => setAdding(false)}
            onCreate={async (input) => {
              await create(input);
              setAdding(false);
            }}
          />
        ) : null}

        {loading ? (
          <div className="py-10 text-center">
            <Spinner label="Loading Digital Twins" />
          </div>
        ) : twins.length === 0 && !adding ? (
          <EmptyState
            title="No Digital Twins yet"
            description="A Digital Twin is a person the assistant answers for. Add one to give answers a role, priorities and a memory of their own."
            action={
              <Button type="button" variant="primary" onClick={() => setAdding(true)}>
                Add Digital Twin
              </Button>
            }
          />
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {twins.map((twin) => {
              const count = counts[twin.id];

              return (
                <li
                  key={twin.id}
                  className="flex flex-col rounded-lg border border-ink-200 px-4 py-3.5"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-ink-900">
                        {twin.name}
                      </p>
                      <p className="truncate text-xs text-ink-500">{twin.role}</p>
                    </div>
                    {twin.id === currentTwin?.id ? (
                      <span className="shrink-0 rounded border border-accent-200 bg-accent-50 px-1.5 py-0.5 text-[0.6875rem] font-medium text-accent-700">
                        Active
                      </span>
                    ) : null}
                  </div>

                  <p className="mt-2.5 text-xs text-ink-500">
                    {count
                      ? `${count.memories} ${count.memories === 1 ? "memory" : "memories"} · ${count.preferences} ${count.preferences === 1 ? "preference" : "preferences"}`
                      : "Loading context…"}
                  </p>

                  <div className="mt-3.5 flex flex-wrap gap-2">
                    <Button type="button" onClick={() => setOpenTwin(twin)}>
                      Open Twin
                    </Button>
                    {twin.id === currentTwin?.id ? null : (
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => select(twin.id)}
                      >
                        Make active
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

/** Only the three fields `UserCreateRequest` accepts. */
function AddTwinForm({
  onCreate,
  onCancel,
}: {
  onCreate: (input: { name: string; email: string; role: string }) => Promise<void>;
  onCancel: () => void;
}) {
  const [values, setValues] = useState({ name: "", email: "", role: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(field: "name" | "email" | "role") {
    return (event: ChangeEvent<HTMLInputElement>) => {
      const next = event.target.value;
      setValues((current) => ({ ...current, [field]: next }));
    };
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);

    try {
      await onCreate(values);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not create the Digital Twin.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-5 rounded-lg border border-ink-200 bg-ink-50/60 px-4 py-4"
    >
      <h2 className="text-sm font-semibold text-ink-900">Add a Digital Twin</h2>
      <p className="mt-0.5 text-xs text-ink-500">
        Name, email and role are what the backend stores for a twin. Profile detail and
        memory are added once it exists.
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <Field label="Name" required value={values.name} onChange={update("name")} />
        <Field
          label="Email"
          required
          type="email"
          value={values.email}
          onChange={update("email")}
        />
        <Field label="Role" required value={values.role} onChange={update("role")} />
      </div>

      {error ? (
        <div className="mt-3">
          <ErrorNotice message={error} />
        </div>
      ) : null}

      <div className="mt-3.5 flex gap-2">
        <Button
          type="submit"
          variant="primary"
          disabled={
            saving || !values.name.trim() || !values.email.trim() || !values.role.trim()
          }
        >
          {saving ? "Creating…" : "Create Digital Twin"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  required,
}: {
  label: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
  type?: string;
  required?: boolean;
}) {
  const id = `field-${label.toLowerCase().replace(/\s+/g, "-")}`;

  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-ink-600">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        required={required}
        onChange={onChange}
        className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
      />
    </div>
  );
}
