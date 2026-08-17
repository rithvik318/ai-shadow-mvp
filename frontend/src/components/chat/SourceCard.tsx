import type { ChatSource } from "../../api/types";
import { describeLocation, formatSimilarity } from "../../lib/format";

/**
 * A retrieved passage, rendered outside the answer.
 *
 * Sources are kept visually separate from the assistant's text on purpose:
 * the backend builds `sources` from retrieved chunks alone, so everything
 * here is a document the model was actually shown. Profile and memory shape
 * the answer but are never citable, and the persona note in the composer says
 * so rather than letting them appear as if they were documents.
 */
export function SourceList({ sources }: { sources: ChatSource[] }) {
  if (sources.length === 0) return null;

  return (
    <section className="mt-3" aria-label="Sources for this answer">
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-500">
        Sources ({sources.length})
      </h3>
      <ul className="space-y-1.5">
        {sources.map((source) => (
          <li key={source.chunk_id}>
            <SourceCard source={source} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function SourceCard({ source }: { source: ChatSource }) {
  const location = describeLocation(source.section, source.page);

  return (
    <article className="rounded-md border border-ink-200 bg-white px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <p
          className="truncate text-sm font-medium text-ink-800"
          title={source.document}
        >
          {source.document}
        </p>
        <span className="shrink-0 text-xs tabular-nums text-ink-500">
          {formatSimilarity(source.similarity)} match
        </span>
      </div>
      {location ? <p className="mt-0.5 text-xs text-ink-500">{location}</p> : null}
    </article>
  );
}
