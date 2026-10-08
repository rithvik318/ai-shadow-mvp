import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";

import { JSDOM } from "jsdom";
import type { Root } from "react-dom/client";

/**
 * What the Reports page does with a report the backend already has.
 *
 * The backend now stores a generated report and serves it back in
 * milliseconds, which only helps if the page asks the right way. Two rules are
 * pinned here, and both are about the difference between reading and
 * rebuilding:
 *
 * **Opening a period reads.** `GET /reports/activity` reuses storage. Nothing
 * about switching tabs, switching back, or re-rendering may issue the `POST`
 * that rebuilds — a rebuild is a mailbox read and a model call per
 * conversation, and it belongs to the button that says so.
 *
 * **A report being built elsewhere is waited for, visibly and finitely.** The
 * `202` answer is not an error and not a spinner: the page says what is
 * happening and how long it has been happening, and asks again.
 */

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});

Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});

Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node,
  Event: dom.window.Event,
  MouseEvent: dom.window.MouseEvent,
  getComputedStyle: dom.window.getComputedStyle,
  requestAnimationFrame: (callback: FrameRequestCallback) =>
    dom.window.setTimeout(() => callback(Date.now()), 0),
  cancelAnimationFrame: (handle: number) => dom.window.clearTimeout(handle),
});

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, StrictMode, act } = await import("react");
const { createRoot } = await import("react-dom/client");

const PERIOD = {
  key: "2026-W37",
  kind: "week",
  label: "7–13 Sep 2026",
  start: "2026-09-07T00:00:00Z",
  end: "2026-09-14T00:00:00Z",
  is_complete: true,
};

const ENVELOPE = {
  report_type: "weekly_email_digest",
  period: PERIOD,
  status: "generated",
  is_provisional: false,
  generated_at: "2026-09-14T08:00:00Z",
  detail: null,
  content: { activity: { sections: [] } },
};

const REPORT = {
  state: "report",
  source: "stored",
  report_version: "1",
  is_provisional: false,
  generated_at: "2026-09-14T08:05:00Z",
  checked_at: "2026-09-14T08:05:00Z",
  data_through: "2026-09-13T23:00:00Z",
  period: PERIOD,
  period_label: PERIOD.label,
  is_partial: false,
  title: "Activity Report",
  markdown: "# Activity Report\n\n## 1. Executive Summary\n\n- Work happened.\n",
  word_count: 1200,
  sections: ["1. Executive Summary"],
  workstream_count: 2,
  thread_count: 9,
  messages_analysed: 83,
  substantive_threads: 9,
  routine_threads: 4,
  narrative: true,
  quality_ok: true,
  quality_notes: [],
  limitations: [],
};

const PENDING = {
  state: "generating",
  period: PERIOD,
  period_label: PERIOD.label,
  started_at: "2026-09-14T08:04:00Z",
  elapsed_seconds: 42,
  detail: "This report is being generated.",
};

const HISTORY = { items: [], total: 0, available_periods: [PERIOD] };
const MAILBOX = {
  address: "owner@sunradia.com",
  provider: "outlook",
  configured: true,
  connected: true,
  shared_fallback: false,
  detail: null,
  capabilities: ["list_messages"],
};
const USERS = {
  items: [
    {
      id: "11111111-1111-4111-8111-111111111111",
      name: "Owner",
      email: "owner@sunradia.com",
      role: "Operations Director",
      is_admin: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  total: 1,
};

let calls: string[] = [];
let activityAnswers: unknown[] = [];
let container: HTMLElement;
let root: Root;

function installFetch() {
  calls = [];
  activityAnswers = [];

  (dom.window as unknown as { fetch: unknown }).fetch = async (
    input: unknown,
    init?: unknown,
  ) => {
    const url = String(
      typeof input === "string" ? input : (input as { url: string }).url,
    );
    const path = url.replace("http://localhost", "").replace(/^\/api/, "");
    const method = String(
      (init as { method?: string } | undefined)?.method ?? "GET",
    ).toUpperCase();
    calls.push(`${method} ${path.split("?")[0]}`);

    if (path.startsWith("/reports/activity")) {
      const answer =
        activityAnswers.length > 1 ? activityAnswers.shift() : activityAnswers[0];

      return new Response(JSON.stringify(answer ?? REPORT), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    const body = path.startsWith("/reports/history")
      ? HISTORY
      : path.startsWith("/reports")
        ? ENVELOPE
        : path.startsWith("/email/mailbox")
          ? MAILBOX
          : path.startsWith("/users")
            ? USERS
            : { items: [], total: 0 };

    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  (globalThis as Record<string, unknown>).fetch = (
    dom.window as unknown as { fetch: unknown }
  ).fetch;
}

function countOf(path: string, method = "GET"): number {
  return calls.filter((call) => call === `${method} ${path}`).length;
}

async function settle(ms = 40) {
  await act(async () => {
    await new Promise((resolve) => dom.window.setTimeout(resolve, ms));
  });
}

async function mount() {
  const { ReportsPanel } = await import("../components/reports/ReportsPanel");
  const { TwinProvider } = await import("../state/TwinContext");

  root = createRoot(container);

  await act(async () => {
    root.render(
      createElement(
        StrictMode,
        null,
        createElement(TwinProvider, null, createElement(ReportsPanel)),
      ),
    );
  });
  await settle();
  await settle();
}

function button(label: string): HTMLElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (element) => element.textContent?.trim() === label,
  );
}

async function click(element: HTMLElement) {
  await act(async () => {
    element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  await settle();
}

beforeEach(() => {
  installFetch();
  container = dom.window.document.createElement("div");
  dom.window.document.body.append(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
});

describe("reading a stored report", () => {
  it("opens a period with a GET and never a POST", async () => {
    activityAnswers = [REPORT];

    await mount();

    assert.equal(countOf("/reports/activity"), 1);
    assert.equal(
      countOf("/reports/activity", "POST"),
      0,
      "opening a period must not rebuild it",
    );
  });

  it("says the report was reused rather than rebuilt", async () => {
    activityAnswers = [REPORT];

    await mount();

    const text = container.textContent ?? "";

    assert.match(text, /Cached/);
    assert.match(text, /Reused from storage/);
  });

  it("says when a report was built for this request", async () => {
    activityAnswers = [{ ...REPORT, source: "generated" }];

    await mount();

    const text = container.textContent ?? "";

    assert.match(text, /Freshly generated/);
    assert.match(text, /stored, so opening this period again/);
  });

  it("marks a report of a period that had not finished", async () => {
    activityAnswers = [{ ...REPORT, is_provisional: true, is_partial: true }];

    await mount();

    assert.match(container.textContent ?? "", /Provisional/);
  });
});

describe("regenerating", () => {
  it("is the only thing that POSTs, and it does it once", async () => {
    activityAnswers = [REPORT];

    await mount();

    const regenerate = button("Regenerate");

    assert.ok(regenerate, "the Regenerate button should be on screen");

    activityAnswers = [{ ...REPORT, source: "generated" }];
    await click(regenerate!);

    assert.equal(countOf("/reports/activity", "POST"), 1);
    assert.match(container.textContent ?? "", /Freshly generated/);
  });

  it("does not happen again when the same period is reopened", async () => {
    activityAnswers = [REPORT];

    await mount();
    await click(button("Regenerate")!);

    const posts = countOf("/reports/activity", "POST");

    await settle(200);

    assert.equal(countOf("/reports/activity", "POST"), posts);
  });
});

describe("when another request is already building it", () => {
  it("says so, with how long it has been going, instead of spinning", async () => {
    activityAnswers = [PENDING, PENDING];

    await mount();

    const text = container.textContent ?? "";

    assert.match(text, /being generated/);
    assert.match(text, /42 seconds ago/);
  });

  it("asks again, and renders the report when it arrives", async () => {
    activityAnswers = [PENDING, REPORT];

    await mount();

    assert.match(container.textContent ?? "", /being generated/);

    // The poll interval is five seconds; jsdom's clock is the real one, so
    // this waits for the first retry rather than pretending to.
    await settle(5_600);

    assert.ok(
      countOf("/reports/activity") >= 2,
      `expected a retry, saw: ${calls.join(", ")}`,
    );
    assert.match(container.textContent ?? "", /Activity Report/);
  });

  it("never turns waiting into a second generation", async () => {
    activityAnswers = [PENDING];

    await mount();
    await settle(5_600);

    assert.equal(countOf("/reports/activity", "POST"), 0);
  });
});
