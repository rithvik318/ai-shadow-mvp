/**
 * The upload result mapping, which is where a backend vocabulary of five
 * outcomes becomes something a person reads. Runnable with `node --test`.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applyResults,
  changedTheCorpus,
  entriesFromFiles,
  presentResult,
  summarise,
} from "../lib/uploads.ts";
import type { BatchUploadResponse } from "../api/types.ts";

function file(name: string, size = 10): File {
  return { name, size } as File;
}

function response(
  items: Array<Partial<BatchUploadResponse["items"][number]>>,
): BatchUploadResponse {
  const full = items.map((item) => ({
    filename: item.filename ?? "a.txt",
    result: item.result ?? "indexed",
    succeeded: item.succeeded ?? true,
    document_id: item.document_id ?? null,
    status: item.status ?? null,
    reason: item.reason ?? null,
  }));

  return {
    items: full,
    total: full.length,
    succeeded: full.filter((i) => i.succeeded).length,
    failed: full.filter((i) => !i.succeeded).length,
  };
}

describe("selection", () => {
  it("lists every selected file before anything is sent", () => {
    const entries = entriesFromFiles([file("a.txt"), file("b.pdf")]);

    assert.equal(entries.length, 2);
    assert.deepEqual(
      entries.map((e) => e.phase),
      ["selected", "selected"],
    );
  });

  it("gives two files with the same name distinct keys", () => {
    // React would otherwise reuse one row for both, and the second file's
    // result would overwrite the first's.
    const entries = entriesFromFiles([file("report.pdf"), file("report.pdf")]);

    assert.notEqual(entries[0].key, entries[1].key);
  });
});

describe("results", () => {
  it("attaches each result to the file that produced it", () => {
    const entries = entriesFromFiles([file("a.txt"), file("b.doc")]);
    const applied = applyResults(
      entries,
      response([
        { result: "indexed", document_id: "doc-1" },
        { result: "unsupported", reason: "Unsupported document type: .doc" },
      ]),
    );

    assert.equal(applied[0].result, "indexed");
    assert.equal(applied[0].documentId, "doc-1");
    assert.equal(applied[1].result, "unsupported");
    assert.match(applied[1].reason ?? "", /Unsupported/);
  });

  it("matches positionally, so duplicate names do not swap outcomes", () => {
    const entries = entriesFromFiles([file("report.pdf"), file("report.pdf")]);
    const applied = applyResults(
      entries,
      response([{ result: "indexed" }, { result: "failed" }]),
    );

    assert.equal(applied[0].result, "indexed");
    assert.equal(applied[1].result, "failed");
  });

  it("marks a file done even if the backend returned fewer items", () => {
    const applied = applyResults(entriesFromFiles([file("a.txt")]), response([]));

    assert.equal(applied[0].phase, "done");
    assert.equal(applied[0].result, undefined);
  });

  it("keeps every outcome the backend distinguishes distinct", () => {
    const labels = (
      ["indexed", "unchanged", "replaced", "unsupported", "failed"] as const
    ).map((r) => presentResult(r).label);

    assert.equal(new Set(labels).size, 5);
  });

  it("does not present unchanged or replaced as failures", () => {
    // They are idempotency working, not something the user must fix.
    assert.notEqual(presentResult("unchanged").tone, "danger");
    assert.notEqual(presentResult("replaced").tone, "danger");
    assert.equal(presentResult("failed").tone, "danger");
    assert.equal(presentResult("unsupported").tone, "warning");
  });
});

describe("knowing when to refresh", () => {
  it("reports a change when something was indexed or replaced", () => {
    assert.equal(changedTheCorpus(response([{ result: "indexed" }])), true);
    assert.equal(changedTheCorpus(response([{ result: "replaced" }])), true);
  });

  it("reports no change when everything was already there or rejected", () => {
    assert.equal(changedTheCorpus(response([{ result: "unchanged" }])), false);
    assert.equal(
      changedTheCorpus(response([{ result: "unsupported" }, { result: "failed" }])),
      false,
    );
  });
});

describe("summary line", () => {
  it("counts each outcome", () => {
    const applied = applyResults(
      entriesFromFiles([file("a"), file("b"), file("c")]),
      response([{ result: "indexed" }, { result: "indexed" }, { result: "failed" }]),
    );

    assert.equal(summarise(applied), "2 indexed, 1 failed");
  });

  it("says nothing before anything has finished", () => {
    assert.equal(summarise(entriesFromFiles([file("a")])), "");
  });
});
