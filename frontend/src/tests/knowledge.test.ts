import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { describeSyncSource, filterDocuments, syncTotals } from "../lib/knowledge.ts";
import type {
  DocumentStatus,
  DocumentSummary,
  SyncSourceState,
  SyncStatusResponse,
} from "../api/types.ts";

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

function source(overrides: Partial<SyncSourceState> = {}): SyncSourceState {
  return {
    source_key: "capabilities",
    label: "Capabilities",
    drive_id: "drive-1",
    item_id: "item-1",
    path: "Documents/Capabilities",
    uri: null,
    enabled: true,
    configured: true,
    status: "succeeded",
    has_delta_token: true,
    last_discovered: 12,
    last_indexed: 3,
    last_replaced: 1,
    last_unchanged: 6,
    last_deleted: 1,
    last_unsupported: 1,
    last_failed: 0,
    error_message: null,
    last_attempted_at: "2026-08-30T09:00:00Z",
    last_succeeded_at: "2026-08-30T09:00:00Z",
    last_duration_ms: 4200,
    ...overrides,
  };
}

function status(sources: SyncSourceState[]): SyncStatusResponse {
  return {
    configured: sources.length > 0,
    scheduled: true,
    interval_seconds: 3600,
    configuration_error: null,
    sources,
  };
}

describe("source status", () => {
  it("shows a synchronised folder as synced", () => {
    const described = describeSyncSource(source());

    assert.equal(described.name, "Capabilities");
    assert.equal(described.label, "Synced");
    assert.equal(described.tone, "positive");
  });

  it("distinguishes a partial run from a failure", () => {
    // The backend held its delta token back rather than losing work. Calling
    // that "failed" would send somebody looking for an outage.
    assert.equal(describeSyncSource(source({ status: "partial" })).label, "Partial");
    assert.equal(describeSyncSource(source({ status: "partial" })).tone, "warning");
    assert.equal(describeSyncSource(source({ status: "failed" })).tone, "danger");
  });

  it("shows a folder configured this morning as not yet synced", () => {
    const described = describeSyncSource(
      source({ status: "never_run", last_succeeded_at: null, last_discovered: 0 }),
    );

    assert.equal(described.label, "Not yet synced");
    assert.equal(described.lastSucceededAt, null);
  });

  it("never reports a disabled source as synced on a stale success", () => {
    // Its last run did succeed. Saying "Synced" would be the screen quietly
    // claiming a folder is current when nothing is watching it.
    const described = describeSyncSource(source({ enabled: false }));

    assert.equal(described.label, "Disabled");
    assert.equal(described.tone, "neutral");
  });

  it("marks a source that is no longer configured", () => {
    assert.equal(
      describeSyncSource(source({ configured: false })).label,
      "No longer configured",
    );
  });

  it("carries the backend's error message and invents none", () => {
    assert.equal(
      describeSyncSource(source({ error_message: "2 file(s) failed transiently." }))
        .detail,
      "2 file(s) failed transiently.",
    );
    assert.equal(describeSyncSource(source()).detail, null);
  });

  it("reports the counts the backend gave, per source", () => {
    const described = describeSyncSource(source());

    assert.equal(described.discovered, 12);
    assert.equal(described.indexed, 3);
    assert.equal(described.unsupported, 1);
    assert.equal(described.failed, 0);
  });
});

describe("source totals", () => {
  const five = [
    source({ source_key: "capabilities", label: "Capabilities" }),
    source({ source_key: "cftc", label: "CFTC / DQ-DA" }),
    source({ source_key: "amtrack", label: "Amtrack / AWS Migration" }),
    source({ source_key: "case-study", label: "Case Study" }),
    source({ source_key: "freddie", label: "Freddie Mac 2026" }),
  ];

  it("counts the five configured sources", () => {
    const totals = syncTotals(status(five));

    assert.equal(totals.sources, 5);
    assert.equal(totals.enabled, 5);
    assert.equal(totals.synced, 5);
    assert.equal(totals.failing, 0);
  });

  it("adds up unsupported and failed files across sources", () => {
    const totals = syncTotals(status(five));

    assert.equal(totals.unsupported, 5);
    assert.equal(totals.failed, 0);
  });

  it("counts a partial run as failing, because something needs attention", () => {
    const totals = syncTotals(
      status([...five.slice(1), source({ status: "partial" })]),
    );

    assert.equal(totals.failing, 1);
    assert.equal(totals.synced, 4);
  });

  it("reports the oldest success as the last full sync", () => {
    // The corpus is only as current as its most stale folder. Showing the
    // newest timestamp would flatter a source that has been failing for days.
    const totals = syncTotals(
      status([
        source({ source_key: "a", last_succeeded_at: "2026-08-30T09:00:00Z" }),
        source({ source_key: "b", last_succeeded_at: "2026-08-24T09:00:00Z" }),
      ]),
    );

    assert.equal(totals.lastSyncedAt, "2026-08-24T09:00:00Z");
  });

  it("claims no last sync while any source has never succeeded", () => {
    const totals = syncTotals(
      status([source(), source({ source_key: "b", last_succeeded_at: null })]),
    );

    assert.equal(totals.lastSyncedAt, null);
  });

  it("counts nothing for an unconfigured deployment", () => {
    const totals = syncTotals(status([]));

    assert.equal(totals.sources, 0);
    assert.equal(totals.lastSyncedAt, null);
  });
});
