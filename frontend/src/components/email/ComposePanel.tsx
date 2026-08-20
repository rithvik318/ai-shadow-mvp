import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
} from "react";

import * as api from "../../api";
import { ApiError } from "../../api/client";
import type {
  ComposeResponse,
  EmailDraft,
  EmailProviderStatus,
  EmailTemplate,
} from "../../api/types";
import {
  REVISION_OPERATIONS,
  approvalBlockers,
  describeDraftStatus,
  invalidRecipients,
  operationBlocker,
  parseRecipients,
  sendBlockers,
} from "../../lib/email";
import { formatBytes } from "../../lib/format";
import { Badge, Button, ErrorNotice, Spinner } from "../ui";

/**
 * The composer: write, generate, revise, save, approve, send.
 *
 * The order of those verbs is the product. Generation produces text and
 * nothing else — it does not save, and it certainly does not send. Saving
 * produces a draft. Approving records that a person read it. Only then does
 * Send appear, and it is disabled with a stated reason whenever the backend
 * would refuse it.
 *
 * `sendBlockers` and `approvalBlockers` are in `lib/email.ts` and mirror the
 * backend's own checks, so the button state and the server agree. When they
 * cannot — a mailbox that drops between render and click — the backend is the
 * authority and its error is shown.
 */
interface Props {
  userId: string;
  twinName: string;
  twinRole: string;
  provider: EmailProviderStatus | null;
  draft: EmailDraft | null;
  onDraftChanged: () => void;
  onClearDraft: () => void;
}

export function ComposePanel({
  userId,
  twinName,
  twinRole,
  provider,
  draft,
  onDraftChanged,
  onClearDraft,
}: Props) {
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [instruction, setInstruction] = useState("");
  const [tone, setTone] = useState("");
  const [useKnowledge, setUseKnowledge] = useState(false);
  const [showCopies, setShowCopies] = useState(false);

  const [saved, setSaved] = useState<EmailDraft | null>(null);
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [lastResult, setLastResult] = useState<ComposeResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Loading a draft handed over from another tab replaces the editor's
  // contents. Deliberately keyed on the draft's id rather than the object, so
  // a refetch of the same draft does not discard what somebody is typing.
  useEffect(() => {
    if (!draft) return;

    setSaved(draft);
    setTo(draft.to_recipients.join(", "));
    setCc(draft.cc_recipients.join(", "));
    setBcc(draft.bcc_recipients.join(", "));
    setSubject(draft.subject);
    setBody(draft.body);
    setShowCopies(draft.cc_recipients.length > 0 || draft.bcc_recipients.length > 0);
    setLastResult(null);
    setError(null);
    setNotice(null);
  }, [draft?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api
      .listEmailTemplates(userId)
      .then((list) => setTemplates(list.items))
      .catch(() => setTemplates([]));
  }, [userId]);

  const recipients = parseRecipients(to);
  const badRecipients = invalidRecipients([
    ...recipients,
    ...parseRecipients(cc),
    ...parseRecipients(bcc),
  ]);

  const report = useCallback((cause: unknown, fallback: string) => {
    setError(cause instanceof ApiError ? cause.message : fallback);
  }, []);

  async function run(operation: string, extra: Record<string, unknown> = {}) {
    setBusy(operation);
    setError(null);
    setNotice(null);

    try {
      const result = await api.composeEmail(userId, {
        operation: operation as ComposeResponse["operation"],
        instruction: instruction.trim() || null,
        subject,
        body,
        tone: tone.trim() || null,
        recipients,
        use_knowledge_base: useKnowledge,
        ...extra,
      });

      setSubject(result.subject || subject);
      setBody(result.body);
      setLastResult(result);
    } catch (cause) {
      report(cause, "The assistant could not produce a draft.");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    setBusy("save");
    setError(null);

    try {
      const payload = {
        to_recipients: recipients,
        cc_recipients: parseRecipients(cc),
        bcc_recipients: parseRecipients(bcc),
        subject,
        body,
      };

      const result = saved
        ? await api.updateEmailDraft(userId, saved.id, payload)
        : await api.createEmailDraft(userId, {
            ...payload,
            generated_by_ai: lastResult !== null,
          });

      setSaved(result);
      setNotice("Draft saved.");
      onDraftChanged();
    } catch (cause) {
      report(cause, "The draft could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  async function approve() {
    if (!saved) return;

    setBusy("approve");
    setError(null);

    try {
      setSaved(await api.approveEmailDraft(userId, saved.id));
      setNotice("Approved. It will be sent only when you press Send.");
      onDraftChanged();
    } catch (cause) {
      report(cause, "The draft could not be approved.");
    } finally {
      setBusy(null);
    }
  }

  async function send() {
    if (!saved) return;

    setBusy("send");
    setError(null);

    try {
      const result = await api.sendEmailDraft(userId, saved.id);
      setSaved(result);
      setNotice("Sent — the mailbox confirmed it.");
      onDraftChanged();
    } catch (cause) {
      report(cause, "The mailbox did not send the message.");
      // Refetch: a failed send moves the draft to `failed` and records why,
      // and the panel must show that rather than the state it had before.
      try {
        setSaved(await api.getEmailDraft(userId, saved.id));
      } catch {
        // Leave the local copy; the error above is the useful message.
      }
    } finally {
      setBusy(null);
    }
  }

  async function attach(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";

    if (files.length === 0) return;

    if (!saved) {
      setError("Save the draft before attaching files.");
      return;
    }

    setBusy("attach");
    setError(null);

    try {
      for (const file of files) {
        await api.attachToEmailDraft(userId, saved.id, file);
      }

      setSaved(await api.getEmailDraft(userId, saved.id));
      onDraftChanged();
    } catch (cause) {
      report(cause, "That file could not be attached.");
    } finally {
      setBusy(null);
    }
  }

  async function detach(attachmentId: string) {
    if (!saved) return;

    try {
      await api.removeEmailAttachment(userId, saved.id, attachmentId);
      setSaved(await api.getEmailDraft(userId, saved.id));
      onDraftChanged();
    } catch (cause) {
      report(cause, "That attachment could not be removed.");
    }
  }

  /**
   * Apply a template to the editor.
   *
   * Named `applyTemplate`, not `useTemplate`. `react-hooks/rules-of-hooks`
   * treats **any** identifier matching `use[A-Z]` as a hook, so the old name
   * made an ordinary event handler look like one and the rule correctly
   * refused to see it called from a `<select onChange>`. Nothing here is a
   * hook: it calls an API and then the setters `useState` already returned,
   * which is what every other handler in this component does.
   */
  async function applyTemplate(templateId: string) {
    if (!templateId) return;

    setBusy("template");
    setError(null);

    try {
      const filled = await api.fillEmailTemplate(userId, templateId, {});
      setSubject(filled.subject);
      setBody(filled.body);
      setNotice(
        filled.missing.length > 0
          ? `Template applied. Still to fill in: ${filled.missing.join(", ")}.`
          : "Template applied.",
      );
    } catch (cause) {
      report(cause, "That template could not be applied.");
    } finally {
      setBusy(null);
    }
  }

  const blockers = saved ? sendBlockers(saved, provider) : ["Save the draft first."];
  const cannotApprove = saved ? approvalBlockers(saved) : ["Save the draft first."];
  const status = saved ? describeDraftStatus(saved.status) : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-ink-200 bg-ink-50/60 px-4 py-3">
        <div>
          <p className="text-sm font-medium text-ink-900">
            Writing as {twinName} · {twinRole}
          </p>
          <p className="mt-0.5 text-xs text-ink-500">
            Their profile, priorities and communication style shape the draft.
            Nobody else&rsquo;s Digital Twin is used.
          </p>
        </div>
        {status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
      </div>

      {error ? <ErrorNotice message={error} /> : null}
      {notice ? (
        <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-sm text-emerald-800">
          {notice}
        </p>
      ) : null}

      {/* --- instruction and generation --- */}
      <div className="rounded-lg border border-ink-200 px-4 py-4">
        <label
          htmlFor="email-instruction"
          className="block text-sm font-medium text-ink-800"
        >
          What should this email do?
        </label>
        <textarea
          id="email-instruction"
          rows={2}
          value={instruction}
          placeholder="Write a concise follow-up after yesterday's meeting and ask for a decision by Friday."
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
            setInstruction(event.target.value)
          }
          className="mt-1.5 w-full rounded-md border border-ink-200 px-3 py-2 text-sm"
        />

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="primary"
            disabled={busy !== null || !instruction.trim()}
            onClick={() => void run("generate")}
          >
            {busy === "generate" ? "Generating…" : "Generate draft"}
          </Button>

          <label className="flex items-center gap-2 text-sm text-ink-600">
            <input
              type="checkbox"
              checked={useKnowledge}
              onChange={(event: ChangeEvent<HTMLInputElement>) =>
                setUseKnowledge(event.target.checked)
              }
            />
            Use the company knowledge base
          </label>

          {templates.length > 0 ? (
            <>
              <label htmlFor="email-template" className="sr-only">
                Start from a template
              </label>
              <select
                id="email-template"
                defaultValue=""
                onChange={(event: ChangeEvent<HTMLSelectElement>) =>
                  void applyTemplate(event.target.value)
                }
                className="rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
              >
                <option value="">Start from a template…</option>
                {templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}
                  </option>
                ))}
              </select>
            </>
          ) : null}
        </div>

        {lastResult ? (
          <div className="mt-3 rounded-md border border-ink-200 bg-white px-3.5 py-2.5">
            <p className="text-xs text-ink-600">
              {lastResult.persona_used
                ? "Written using this twin's profile and memories."
                : "This twin has no profile yet, so the draft uses a neutral voice."}{" "}
              {lastResult.knowledge_used
                ? `Grounded in ${lastResult.sources.length} passage${lastResult.sources.length === 1 ? "" : "s"} from the knowledge base.`
                : "No company knowledge was retrieved, so the draft makes no claims about SunRadia."}
            </p>
            {lastResult.sources.length > 0 ? (
              <ul className="mt-1.5 space-y-0.5">
                {lastResult.sources.map((source) => (
                  <li key={source.chunk_id} className="text-xs text-ink-500">
                    {source.document}
                    {source.section ? ` · ${source.section}` : ""}
                    {source.page !== null ? ` · p.${source.page}` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* --- revision operations --- */}
      <div className="flex flex-wrap items-center gap-2">
        {REVISION_OPERATIONS.map((choice) => {
          const blocker = operationBlocker(choice, { body, tone });

          return (
            <Button
              key={choice.operation}
              type="button"
              title={blocker ?? undefined}
              disabled={busy !== null || blocker !== null}
              onClick={() => void run(choice.operation)}
            >
              {busy === choice.operation ? "Working…" : choice.label}
            </Button>
          );
        })}
        <input
          type="text"
          value={tone}
          placeholder="Tone, e.g. warmer"
          aria-label="Requested tone"
          onChange={(event: ChangeEvent<HTMLInputElement>) => setTone(event.target.value)}
          className="w-44 rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
        />
      </div>

      {/* --- the email itself --- */}
      <div className="space-y-3 rounded-lg border border-ink-200 px-4 py-4">
        <Field label="To" value={to} onChange={setTo} placeholder="client@example.com" />

        {showCopies ? (
          <>
            <Field label="Cc" value={cc} onChange={setCc} />
            <Field label="Bcc" value={bcc} onChange={setBcc} />
          </>
        ) : (
          <button
            type="button"
            onClick={() => setShowCopies(true)}
            className="text-xs font-medium text-accent-700 hover:underline"
          >
            Add Cc / Bcc
          </button>
        )}

        {badRecipients.length > 0 ? (
          <p className="text-xs text-rose-700">
            Not an email address: {badRecipients.join(", ")}
          </p>
        ) : null}

        <Field label="Subject" value={subject} onChange={setSubject} />

        <div>
          <label htmlFor="email-body" className="block text-xs font-medium text-ink-600">
            Message
          </label>
          <textarea
            id="email-body"
            rows={14}
            value={body}
            onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
              setBody(event.target.value)
            }
            className="mt-1 w-full rounded-md border border-ink-200 px-3 py-2 font-sans text-sm leading-relaxed"
          />
        </div>

        {/* --- attachments --- */}
        <div>
          <p className="text-xs font-medium text-ink-600">Attachments</p>
          {saved && saved.attachments.length > 0 ? (
            <ul className="mt-1.5 space-y-1">
              {saved.attachments.map((attachment) => (
                <li
                  key={attachment.id}
                  className="flex items-center justify-between gap-3 rounded-md border border-ink-200 px-2.5 py-1.5"
                >
                  <span className="min-w-0 truncate text-sm text-ink-800">
                    {attachment.filename}{" "}
                    <span className="text-xs text-ink-500">
                      {formatBytes(attachment.size_bytes)}
                    </span>
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => void detach(attachment.id)}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}

          <label className="mt-1.5 inline-flex cursor-pointer items-center gap-2 text-sm text-accent-700 hover:underline">
            <input type="file" multiple className="sr-only" onChange={attach} />
            {busy === "attach" ? "Attaching…" : "Attach a file"}
          </label>
          {!saved ? (
            <p className="mt-1 text-xs text-ink-400">
              Save the draft first — an attachment belongs to a draft.
            </p>
          ) : null}
        </div>
      </div>

      {/* --- the review gate --- */}
      <div className="rounded-lg border border-ink-200 px-4 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="primary"
            disabled={busy !== null || badRecipients.length > 0}
            onClick={() => void save()}
          >
            {busy === "save" ? "Saving…" : saved ? "Save changes" : "Save draft"}
          </Button>

          <Button
            type="button"
            disabled={busy !== null || cannotApprove.length > 0}
            title={cannotApprove[0]}
            onClick={() => void approve()}
          >
            {busy === "approve" ? "Approving…" : "Approve for sending"}
          </Button>

          <Button
            type="button"
            variant="danger"
            disabled={busy !== null || blockers.length > 0}
            title={blockers[0]}
            onClick={() => void send()}
          >
            {busy === "send" ? "Sending…" : "Send"}
          </Button>

          {saved ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onClearDraft();
                setSaved(null);
                setTo("");
                setCc("");
                setBcc("");
                setSubject("");
                setBody("");
                setInstruction("");
                setLastResult(null);
                setNotice(null);
              }}
            >
              Start a new email
            </Button>
          ) : null}

          {busy !== null ? <Spinner label="Working" /> : null}
        </div>

        {blockers.length > 0 ? (
          <ul className="mt-2.5 space-y-0.5">
            {blockers.map((blocker) => (
              <li key={blocker} className="text-xs text-ink-500">
                · {blocker}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2.5 text-xs text-ink-500">
            Approved and ready. Sending is irreversible.
          </p>
        )}

        {saved?.send_error ? (
          <p className="mt-2 text-xs text-rose-700">
            The mailbox refused this: {saved.send_error}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const id = `email-${label.toLowerCase()}`;

  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-ink-600">
        {label}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.value)}
        className="mt-1 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
      />
    </div>
  );
}
