import type { ReactNode } from "react";

import type { ActivityReport } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { Badge } from "../ui";

/**
 * The detailed activity report, and what it was built from.
 *
 * The header strip is not decoration. A reader who cannot see whether a
 * narrative analysis contributed, how much of the mailbox was substantive, or
 * whether the quality check passed has no way to tell a genuinely quiet month
 * from a report that failed to say anything — which is exactly the confusion
 * the previous count-based report created.
 *
 * Markdown is rendered here rather than by a library on purpose. One function
 * in `composition.py` produces every one of these documents, so the grammar is
 * known and closed: headings, bullets, pipe tables, `**bold**`, `` `code` ``
 * and the `<br/>` that separates a table cell from its evidence line. A
 * dependency to parse a language we ourselves emit would be a larger change
 * than the integration it serves, and this file would still need the styling.
 *
 * Nothing here is `dangerouslySetInnerHTML`. The report contains subject lines
 * and excerpts written by other people, so it is untrusted text and is
 * rendered as text.
 */
/** What each `source` means, in the two or three words a badge has room for. */
const PROVENANCE: Record<ActivityReport["source"], string> = {
  generated: "Freshly generated",
  revalidated: "Cached · rechecked",
  stored: "Cached",
};

export function ActivityReportDocument({ report }: { report: ActivityReport }) {
  return (
    <div>
      <div className="mb-4 rounded-lg border border-ink-200 bg-ink-50 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="info">{report.period_label}</Badge>
          {report.is_partial ? <Badge tone="warning">Partial period</Badge> : null}
          <Badge tone={report.source === "generated" ? "positive" : "neutral"}>
            {PROVENANCE[report.source]}
          </Badge>
          {report.is_provisional ? <Badge tone="warning">Provisional</Badge> : null}
          <Badge tone={report.narrative ? "positive" : "neutral"}>
            {report.narrative ? "Narrative analysis" : "Structural analysis only"}
          </Badge>
          {report.quality_ok ? null : (
            <Badge tone="warning">Below expected detail</Badge>
          )}
        </div>

        <dl className="mt-2.5 grid gap-x-6 gap-y-1 text-xs text-ink-600 sm:grid-cols-3 lg:grid-cols-5">
          <Pair label="Messages analysed" value={report.messages_analysed} />
          <Pair label="Substantive threads" value={report.substantive_threads} />
          <Pair label="Routine / automated" value={report.routine_threads} />
          <Pair label="Workstreams" value={report.workstream_count} />
          <Pair label="Conversations described" value={report.thread_count} />
        </dl>

        {/* Three different instants, and conflating them is how a reader ends
            up trusting a month-old document because the page was opened just
            now. When the document was built, what the evidence runs to, and —
            for a report that was reused — when that evidence was last
            confirmed. */}
        <p className="mt-2 text-xs text-ink-500">
          Generated {formatDateTime(report.generated_at)}
          {report.data_through
            ? `, from correspondence through ${formatDateTime(report.data_through)}`
            : " from correspondence"}
          .{" "}
          {report.source === "generated"
            ? "Built for this request and stored, so opening this period again reads it back rather than rebuilding it."
            : report.source === "revalidated"
              ? "Reused from storage: the mailbox was re-read just now and holds nothing new for this period."
              : "Reused from storage. Use Regenerate to rebuild it from the mailbox."}
        </p>

        {report.limitations.length > 0 ? (
          <Notes title="Known limitations" items={report.limitations} tone="amber" />
        ) : null}

        {report.quality_notes.length > 0 ? (
          <Notes title="Quality notes" items={report.quality_notes} tone="ink" />
        ) : null}
      </div>

      <article className="max-w-none text-sm text-ink-800">
        {renderMarkdown(report.markdown)}
      </article>
    </div>
  );
}

function Pair({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-ink-500">{label}</dt>
      <dd className="font-medium text-ink-800">{value}</dd>
    </div>
  );
}

function Notes({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "amber" | "ink";
}) {
  return (
    <details className="mt-2">
      <summary
        className={`cursor-pointer text-xs font-medium ${
          tone === "amber" ? "text-amber-700" : "text-ink-600"
        }`}
      >
        {title} ({items.length})
      </summary>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-ink-600">
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    </details>
  );
}

/** `**bold**`, `*italic*`, `` `code` `` and `<br/>`, as React nodes. */
function inline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|<br\/>)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-${index++}`;

    if (token === "<br/>") {
      nodes.push(<br key={key} />);
    } else if (token.startsWith("**")) {
      nodes.push(
        <strong key={key} className="font-semibold text-ink-900">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code
          key={key}
          className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[11px] text-ink-700"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      nodes.push(
        <em key={key} className="text-ink-600">
          {token.slice(1, -1)}
        </em>,
      );
    }

    last = match.index + token.length;
  }

  if (last < text.length) {
    nodes.push(text.slice(last));
  }

  return nodes;
}

function cells(row: string): string[] {
  return row
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** A separator row — `|---|---|` — which marks the line above it as a header. */
function isDivider(row: string): boolean {
  return /^\|?[\s:|-]+\|[\s:|-]*$/.test(row) && row.includes("-");
}

export function renderMarkdown(markdown: string): ReactNode[] {
  const lines = markdown.split("\n");
  const out: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    // A table: a pipe row, optionally followed by a divider and body rows.
    if (line.trim().startsWith("|")) {
      const block: string[] = [];

      while (index < lines.length && lines[index].trim().startsWith("|")) {
        block.push(lines[index]);
        index += 1;
      }

      const hasHeader = block.length > 1 && isDivider(block[1]);
      const head = hasHeader ? cells(block[0]) : null;
      const body = block.slice(hasHeader ? 2 : 0).filter((row) => !isDivider(row));

      out.push(
        <div key={`table-${index}`} className="my-3 overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs">
            {head ? (
              <thead>
                <tr>
                  {head.map((cell, column) => (
                    <th
                      key={column}
                      className="border-b border-ink-300 px-2 py-1.5 font-semibold text-ink-700"
                    >
                      {inline(cell, `th-${column}`)}
                    </th>
                  ))}
                </tr>
              </thead>
            ) : null}
            <tbody>
              {body.map((row, rowIndex) => (
                <tr key={rowIndex} className="align-top">
                  {cells(row).map((cell, column) => (
                    <td
                      key={column}
                      className="border-b border-ink-100 px-2 py-1.5 text-ink-700"
                    >
                      {inline(cell, `td-${rowIndex}-${column}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );

      continue;
    }

    if (line.startsWith("- ")) {
      const items: string[] = [];

      while (index < lines.length && lines[index].startsWith("- ")) {
        items.push(lines[index].slice(2));
        index += 1;

        // A `  - ` continuation belongs to the bullet above it, which is how
        // the header's limitations are nested.
        while (index < lines.length && lines[index].startsWith("  - ")) {
          items.push(`— ${lines[index].slice(4)}`);
          index += 1;
        }
      }

      out.push(
        <ul key={`ul-${index}`} className="my-2 list-disc space-y-1 pl-5">
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{inline(item, `li-${itemIndex}`)}</li>
          ))}
        </ul>,
      );

      continue;
    }

    if (line.startsWith("### ")) {
      out.push(
        <h4 key={`h4-${index}`} className="mt-4 text-sm font-semibold text-ink-900">
          {line.slice(4)}
        </h4>,
      );
    } else if (line.startsWith("## ")) {
      out.push(
        <h3
          key={`h3-${index}`}
          className="mt-6 border-b border-ink-200 pb-1 text-base font-semibold text-ink-900"
        >
          {line.slice(3)}
        </h3>,
      );
    } else if (line.startsWith("# ")) {
      out.push(
        <h2 key={`h2-${index}`} className="text-lg font-semibold text-ink-900">
          {line.slice(2)}
        </h2>,
      );
    } else {
      out.push(
        <p key={`p-${index}`} className="my-2 leading-relaxed">
          {inline(line, `p-${index}`)}
        </p>,
      );
    }

    index += 1;
  }

  return out;
}
