import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import {
  EMAIL_TEMPLATE_CATEGORIES,
  type EmailDraft,
  type EmailTemplate,
  type EmailTemplateCategory,
} from "../../api/types";
import { missingPlaceholders, searchTemplates } from "../../lib/email";
import { Badge, Button, EmptyState, ErrorNotice, Spinner } from "../ui";

/**
 * User-owned templates: list, search, create, edit, delete, and use.
 *
 * "Use" fills the placeholders and opens the result as a draft. Filling is
 * substitution on the server, not a model call — paying for a completion to
 * replace `{{ name }}` with a name would be slower, dearer and less
 * predictable. Generating *from* a filled template is the composer's job.
 *
 * A placeholder left empty stays visible as `{{ name }}` in the draft rather
 * than being blanked, so a person proof-reading sees what they still owe.
 */
interface Props {
  userId: string;
  onUseTemplate: (draft: EmailDraft) => void;
}

const CATEGORY_LABELS: Record<EmailTemplateCategory, string> = {
  introduction: "Introduction",
  follow_up: "Follow-up",
  meeting_request: "Meeting request",
  proposal_follow_up: "Proposal follow-up",
  thank_you: "Thank you",
  outreach: "Outreach",
  custom: "Custom",
};

export function TemplatesPanel({ userId, onUseTemplate }: Props) {
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EmailTemplate | "new" | null>(null);
  const [filling, setFilling] = useState<EmailTemplate | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const list = await api.listEmailTemplates(userId);
      setTemplates(list.items);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Templates could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove(template: EmailTemplate) {
    if (!window.confirm(`Delete the template "${template.name}"?`)) return;

    try {
      await api.deleteEmailTemplate(userId, template.id);
      setTemplates((current) => current.filter((item) => item.id !== template.id));
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "That template could not be deleted.",
      );
    }
  }

  const visible = searchTemplates(templates, query);

  if (filling) {
    return (
      <FillForm
        userId={userId}
        template={filling}
        onCancel={() => setFilling(null)}
        onDrafted={(draft) => {
          setFilling(null);
          onUseTemplate(draft);
        }}
      />
    );
  }

  if (editing) {
    return (
      <TemplateForm
        userId={userId}
        template={editing === "new" ? null : editing}
        onCancel={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          void load();
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink-900">
          Templates <span className="font-normal text-ink-500">({visible.length})</span>
        </h2>
        <div className="flex items-center gap-2">
          <label htmlFor="template-search" className="sr-only">
            Search templates
          </label>
          <input
            id="template-search"
            type="search"
            value={query}
            placeholder="Search templates…"
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              setQuery(event.target.value)
            }
            className="w-52 rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
          />
          <Button type="button" variant="primary" onClick={() => setEditing("new")}>
            New template
          </Button>
        </div>
      </div>

      {error ? <ErrorNotice message={error} onRetry={() => void load()} /> : null}

      {loading ? (
        <div className="py-10 text-center">
          <Spinner label="Loading templates" />
        </div>
      ) : templates.length === 0 ? (
        <EmptyState
          title="No templates yet"
          description="A template is a reusable subject and body with placeholders like {{ name }}. Templates are yours alone — nobody else sees them."
          action={
            <Button type="button" variant="primary" onClick={() => setEditing("new")}>
              Create a template
            </Button>
          }
        />
      ) : visible.length === 0 ? (
        <p className="rounded-md border border-dashed border-ink-300 px-4 py-8 text-center text-sm text-ink-500">
          No templates match that search.
        </p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((template) => (
            <li
              key={template.id}
              className="flex flex-col rounded-lg border border-ink-200 px-4 py-3.5"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 truncate text-sm font-semibold text-ink-900">
                  {template.name}
                </p>
                <Badge tone="neutral">{CATEGORY_LABELS[template.category]}</Badge>
              </div>

              {template.description ? (
                <p className="mt-1 text-xs text-ink-500">{template.description}</p>
              ) : null}

              <p className="mt-2 line-clamp-2 text-xs text-ink-600">
                {template.subject_template}
              </p>

              {template.placeholders.length > 0 ? (
                <p className="mt-2 text-[0.6875rem] text-ink-400">
                  Fills in: {template.placeholders.join(", ")}
                </p>
              ) : null}

              <div className="mt-3.5 flex flex-wrap gap-2">
                <Button type="button" onClick={() => setFilling(template)}>
                  Use
                </Button>
                <Button type="button" variant="ghost" onClick={() => setEditing(template)}>
                  Edit
                </Button>
                <Button type="button" variant="danger" onClick={() => void remove(template)}>
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TemplateForm({
  userId,
  template,
  onCancel,
  onSaved,
}: {
  userId: string;
  template: EmailTemplate | null;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(template?.name ?? "");
  const [description, setDescription] = useState(template?.description ?? "");
  const [category, setCategory] = useState<EmailTemplateCategory>(
    template?.category ?? "custom",
  );
  const [subject, setSubject] = useState(template?.subject_template ?? "");
  const [body, setBody] = useState(template?.body_template ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);

    try {
      const payload = {
        name,
        description: description.trim() || null,
        category,
        subject_template: subject,
        body_template: body,
      };

      if (template) {
        await api.updateEmailTemplate(userId, template.id, payload);
      } else {
        await api.createEmailTemplate(userId, payload);
      }

      onSaved();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "That template could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-ink-200 px-4 py-4">
      <h2 className="text-sm font-semibold text-ink-900">
        {template ? `Edit "${template.name}"` : "New template"}
      </h2>
      <p className="text-xs text-ink-500">
        Write placeholders as <code>{"{{ name }}"}</code>. They are filled in when the
        template is used, and any left empty stay visible in the draft rather than
        turning into a blank.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <Labelled label="Name">
          <input
            type="text"
            required
            value={name}
            onChange={(event: ChangeEvent<HTMLInputElement>) => setName(event.target.value)}
            className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
          />
        </Labelled>

        <Labelled label="Category">
          <select
            value={category}
            onChange={(event: ChangeEvent<HTMLSelectElement>) =>
              setCategory(event.target.value as EmailTemplateCategory)
            }
            className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
          >
            {EMAIL_TEMPLATE_CATEGORIES.map((item) => (
              <option key={item} value={item}>
                {CATEGORY_LABELS[item]}
              </option>
            ))}
          </select>
        </Labelled>
      </div>

      <Labelled label="Description">
        <input
          type="text"
          value={description}
          onChange={(event: ChangeEvent<HTMLInputElement>) =>
            setDescription(event.target.value)
          }
          className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
        />
      </Labelled>

      <Labelled label="Subject">
        <input
          type="text"
          value={subject}
          onChange={(event: ChangeEvent<HTMLInputElement>) => setSubject(event.target.value)}
          className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
        />
      </Labelled>

      <Labelled label="Body">
        <textarea
          rows={12}
          required
          value={body}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setBody(event.target.value)}
          className="mt-1 w-full rounded-md border border-ink-200 px-3 py-2 text-sm leading-relaxed"
        />
      </Labelled>

      {error ? <ErrorNotice message={error} /> : null}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={saving || !name.trim() || !body.trim()}>
          {saving ? "Saving…" : "Save template"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function FillForm({
  userId,
  template,
  onCancel,
  onDrafted,
}: {
  userId: string;
  template: EmailTemplate;
  onCancel: () => void;
  onDrafted: (draft: EmailDraft) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const missing = missingPlaceholders(template, values);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const filled = await api.fillEmailTemplate(userId, template.id, values);
      const draft = await api.createEmailDraft(userId, {
        subject: filled.subject,
        body: filled.body,
        template_id: template.id,
      });

      onDrafted(draft);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The template could not be used.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-ink-200 px-4 py-4">
      <h2 className="text-sm font-semibold text-ink-900">Use &ldquo;{template.name}&rdquo;</h2>

      {template.placeholders.length === 0 ? (
        <p className="text-xs text-ink-500">
          This template has no placeholders — it will be used as written.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {template.placeholders.map((placeholder) => (
            <Labelled key={placeholder} label={placeholder}>
              <input
                type="text"
                value={values[placeholder] ?? ""}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  setValues((current) => ({
                    ...current,
                    [placeholder]: event.target.value,
                  }))
                }
                className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
              />
            </Labelled>
          ))}
        </div>
      )}

      {missing.length > 0 ? (
        <p className="text-xs text-ink-500">
          Still empty: {missing.join(", ")}. You can carry on — they stay visible in
          the draft as <code>{"{{ … }}"}</code> so you can spot them.
        </p>
      ) : null}

      {error ? <ErrorNotice message={error} /> : null}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? "Preparing…" : "Open as a draft"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-ink-600">{label}</span>
      {children}
    </label>
  );
}
