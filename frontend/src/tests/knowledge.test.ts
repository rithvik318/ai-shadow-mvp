import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { filterDocuments, statsFor } from "../lib/knowledge.ts";
import type { DocumentStatus, DocumentSummary } from "../api/types.ts";

function doc(
  filename: string,
  status: DocumentStatus,
  sourceUri: string | null = null,
): DocumentSummary {
  return {
    id: filename,
    filename,
    content_type: "text/plain",
    file_size_bytes: 100,
    page_count: null,
    chunk_count: 3,
    status,
    error_message: null,
    content_hash: null,
    source_uri: sourceUri,
    source_version: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

describe("statistics", () => {
  it("counts by the backend's own statuses", () => {
    const stats = statsFor(
      [
        doc("a", "indexed"),
        doc("b", "indexed"),
        doc("c", "failed"),
        doc("d", "unsupported"),
      ],
      4,
    );

    assert.equal(stats.byStatus.indexed, 2);
    assert.equal(stats.byStatus.failed, 1);
    assert.equal(stats.byStatus.unsupported, 1);
  });

  it("says when the counts describe only part of the corpus", () => {
    // The backend exposes no counts endpoint, so a partial page must not be
    // presented as a total.
    assert.equal(statsFor([doc("a", "indexed")], 786).complete, false);
    assert.equal(statsFor([doc("a", "indexed")], 1).complete, true);
  });

  it("reports zeroes rather than nothing for an empty corpus", () => {
    const stats = statsFor([], 0);

    assert.equal(stats.total, 0);
    assert.equal(stats.byStatus.indexed, 0);
  });
});

describe("filtering", () => {
  const documents = [
    doc("Freddie Mac proposal.pdf", "indexed"),
    doc("capabilities.docx", "indexed"),
    doc("broken.pdf", "failed"),
  ];

  it("matches filenames case-insensitively", () => {
    assert.equal(filterDocuments(documents, { query: "freddie" }).length, 1);
  });

  it("filters by status", () => {
    assert.equal(filterDocuments(documents, { status: "failed" }).length, 1);
  });

  it("combines both", () => {
    assert.equal(
      filterDocuments(documents, { query: "pdf", status: "indexed" }).length,
      1,
    );
  });

  it("returns everything with no filters", () => {
    assert.equal(filterDocuments(documents).length, 3);
  });
});
