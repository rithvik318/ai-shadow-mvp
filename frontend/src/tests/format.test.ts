import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  describeLocation,
  describeSource,
  formatBytes,
  formatSimilarity,
} from "../lib/format.ts";

describe("citation location", () => {
  it("names the page and the heading when both are known", () => {
    assert.equal(describeLocation("Water Treatment", 4), "Page 4 · Water Treatment");
  });

  it("falls back to the heading when the format has no pages", () => {
    // DOCX carries no page number without rendering the file.
    assert.equal(describeLocation("Scope of Work", null), "Scope of Work");
  });

  it("says nothing rather than inventing a location", () => {
    assert.equal(describeLocation(null, null), "");
  });

  it("keeps page zero visible", () => {
    assert.equal(describeLocation(null, 0), "Page 0");
  });
});

describe("source", () => {
  it("distinguishes a synced document from an uploaded one", () => {
    assert.equal(describeSource("onedrive:drive:item"), "OneDrive");
    assert.equal(describeSource(null), "Uploaded");
  });
});

describe("numbers", () => {
  it("formats sizes", () => {
    assert.equal(formatBytes(512), "512 B");
    assert.equal(formatBytes(2048), "2.0 KB");
    assert.equal(formatBytes(5 * 1024 * 1024), "5.0 MB");
  });

  it("does not crash on a missing size", () => {
    assert.equal(formatBytes(Number.NaN), "—");
  });

  it("shows similarity as a percentage", () => {
    assert.equal(formatSimilarity(0.8123), "81%");
  });
});
