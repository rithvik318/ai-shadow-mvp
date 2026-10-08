import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import { renderMarkdown } from "../components/reports/ActivityReportDocument";

/**
 * The renderer is fed the grammar `composition.py` actually emits, not a
 * general markdown corpus. That is the whole reason it can be this small: one
 * function on the backend produces every document this component will ever be
 * given, so the surface is closed and testable against a real sample.
 */
const SAMPLE = `# Activity Report — Pat Owner

- **Mailbox:** pat@sunradia.com
- **Known limitations:**
  - Calendar not readable: Graph denied the request (403).

## 1. Executive Summary

- Property refurbishment quoting progressed.

## 2. Major Workstreams / Business Activities

### Factory Lane refurbishment

- **Scope:** Bring the site up to specification.

## 3. Important Conversations / Threads

| Conversation | Why it matters | Status |
|---|---|---|
| Factory Lane quotation | Affects the budget | awaiting external response <br/>Evidence: \`AAMkAGVhNmJ\` |

## 7. Deadlines and Important Dates

*No dates were explicitly stated in the correspondence for this period.*
`;

function html(markdown: string): string {
  return renderToStaticMarkup(<>{renderMarkdown(markdown)}</>);
}

describe("activity report rendering", () => {
  it("renders the document title as a heading", () => {
    assert.match(html(SAMPLE), /<h2[^>]*>Activity Report — Pat Owner<\/h2>/);
  });

  it("renders every numbered section heading", () => {
    const out = html(SAMPLE);

    for (const title of [
      "1. Executive Summary",
      "2. Major Workstreams / Business Activities",
      "3. Important Conversations / Threads",
      "7. Deadlines and Important Dates",
    ]) {
      assert.ok(out.includes(title), `missing section: ${title}`);
    }
  });

  it("renders a workstream sub-heading", () => {
    assert.match(html(SAMPLE), /<h4[^>]*>Factory Lane refurbishment<\/h4>/);
  });

  it("renders bullets as a list, including nested limitations", () => {
    const out = html(SAMPLE);

    assert.ok(out.includes("<ul"));
    assert.ok(out.includes("Calendar not readable"));
  });

  it("renders a pipe table with a header row", () => {
    const out = html(SAMPLE);

    assert.ok(out.includes("<table"));
    assert.ok(out.includes("<th"));
    assert.ok(out.includes("Why it matters"));
    assert.ok(out.includes("Factory Lane quotation"));
  });

  it("does not render the table divider row as data", () => {
    assert.ok(!html(SAMPLE).includes("---"));
  });

  it("preserves the evidence citation in a table cell", () => {
    const out = html(SAMPLE);

    assert.ok(out.includes("Evidence:"));
    assert.match(out, /<code[^>]*>AAMkAGVhNmJ<\/code>/);
  });

  it("renders bold labels as strong rather than as asterisks", () => {
    const out = html(SAMPLE);

    assert.ok(out.includes("<strong"));
    assert.ok(!out.includes("**"));
  });

  it("renders an empty-section note as emphasis rather than dropping it", () => {
    // A heading with nothing under it is a finding; a missing heading reads as
    // a broken report, which is what the old renderer produced.
    assert.match(html(SAMPLE), /<em[^>]*>No dates were explicitly stated/);
  });

  it("escapes text rather than injecting it as markup", () => {
    // Report content includes subject lines written by other people.
    const out = html("- <img src=x onerror=alert(1)>");

    assert.ok(!out.includes("<img"));
    assert.ok(out.includes("&lt;img"));
  });

  it("returns nothing for an empty document rather than throwing", () => {
    assert.equal(html(""), "");
  });
});
