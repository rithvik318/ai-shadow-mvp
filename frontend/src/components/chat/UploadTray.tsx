import { useRef, useState, type ChangeEvent } from "react";

import * as api from "../../api";
import {
  applyResults,
  changedTheCorpus,
  entriesFromFiles,
  presentResult,
  summarise,
  type UploadEntry,
} from "../../lib/uploads";
import { formatBytes } from "../../lib/format";
import { Badge, Button, ErrorNotice, Spinner } from "../ui";

/**
 * Attaching documents without leaving the conversation.
 *
 * Selection, progress and per-file outcome all happen in the composer, so
 * uploading a file is part of asking a question rather than a detour into a
 * different screen.
 */
export function UploadTray({ onIngested }: { onIngested: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<File[]>([]);

  function onSelect(files: FileList | null) {
    if (!files || files.length === 0) return;

    const chosen = Array.from(files);
    setPending(chosen);
    setEntries(entriesFromFiles(chosen));
    setError(null);
  }

  function reset() {
    setEntries([]);
    setPending([]);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  async function send() {
    if (pending.length === 0) return;

    setBusy(true);
    setError(null);
    setEntries((current) => current.map((entry) => ({ ...entry, phase: "uploading" })));

    try {
      const response = await api.uploadDocuments(pending);
      setEntries((current) => applyResults(current, response));
      setPending([]);
      if (inputRef.current) inputRef.current.value = "";

      // Only reload the corpus when it actually changed; a batch of
      // already-known files should not make the documents panel flicker.
      if (changedTheCorpus(response)) onIngested();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Upload failed.");
      setEntries((current) =>
        current.map((entry) => ({ ...entry, phase: "selected" })),
      );
    } finally {
      setBusy(false);
    }
  }

  const summary = summarise(entries);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={inputRef}
          id="chat-file-input"
          type="file"
          multiple
          className="sr-only"
          accept=".pdf,.docx,.pptx,.txt,.md,.markdown"
          onChange={(event: ChangeEvent<HTMLInputElement>) =>
            onSelect(event.target.files)
          }
        />
        <Button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          aria-controls="upload-tray-list"
        >
          Attach documents
        </Button>

        {pending.length > 0 ? (
          <>
            <Button
              variant="primary"
              type="button"
              onClick={() => void send()}
              disabled={busy}
            >
              {busy
                ? "Uploading…"
                : `Upload ${pending.length} file${pending.length === 1 ? "" : "s"}`}
            </Button>
            <Button variant="ghost" type="button" onClick={reset} disabled={busy}>
              Clear
            </Button>
          </>
        ) : null}

        {busy ? <Spinner label="Indexing" /> : null}
        {summary && !busy ? (
          <span className="text-xs text-ink-500">{summary}</span>
        ) : null}
        {entries.length > 0 && !busy && pending.length === 0 ? (
          <Button variant="ghost" type="button" onClick={reset}>
            Dismiss
          </Button>
        ) : null}
      </div>

      {error ? <ErrorNotice message={error} onRetry={() => void send()} /> : null}

      {entries.length > 0 ? (
        <ul
          id="upload-tray-list"
          className="space-y-1 rounded-md border border-ink-200 bg-white p-2"
        >
          {entries.map((entry) => (
            <li
              key={entry.key}
              className="flex items-center justify-between gap-3 px-1 py-1"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-ink-800" title={entry.filename}>
                  {entry.filename}
                </p>
                <p className="text-xs text-ink-500">
                  {formatBytes(entry.sizeBytes)}
                  {entry.reason ? ` · ${entry.reason}` : ""}
                </p>
              </div>
              <EntryStatus entry={entry} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function EntryStatus({ entry }: { entry: UploadEntry }) {
  if (entry.phase === "selected") return <Badge>Ready</Badge>;
  if (entry.phase === "uploading") return <Badge tone="info">Uploading…</Badge>;
  if (!entry.result) return <Badge>Done</Badge>;

  const presentation = presentResult(entry.result);

  return (
    <span title={presentation.description}>
      <Badge tone={presentation.tone}>{presentation.label}</Badge>
    </span>
  );
}
