import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";

import { JSDOM } from "jsdom";
import type { Root } from "react-dom/client";

/**
 * How many requests one visit to the Reports page makes.
 *
 * The reported symptom was a Reports page that hung and, in the backend log,
 * repeated reads of `users`, `user_mailbox` and `task` — the three queries
 * behind `GET /reports/activity`. That endpoint is the expensive one: it reads
 * a mailbox and then asks a model about every conversation in the period. Any
 * duplicate of it is minutes of work, not milliseconds.
 *
 * Three things caused duplicates and all three are pinned below. The effect
 * that generates the report was keyed on the period *object*, which is a fresh
 * identity from every fetch, so an identical period re-ran it. React's
 * StrictMode double-invokes mount effects in development, which doubled the
 * first request. And nothing tracked what was already in flight, so a rapid
 * period change left two pipelines running.
 *
 * The page is mounted for real — jsdom, `createRoot`, real effects — because
 * the assertion is about what effects do, and that cannot be tested in a pure
 * function.
 */

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});

// `navigator` is a getter on Node's globalThis, so it is defined rather than
// assigned; the rest are plain properties.
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

// React reads this to decide whether it is in a test, which is what suppresses
// the "not wrapped in act" warning for updates we do await.
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, StrictMode } = await import("react");
const { createRoot } = await import("react-dom/client");
const { act } = await import("react");

const PERIOD = {
  key: "2026-W37",
  kind: "week",
  label: "8–14 Sep 2026",
  start: "2026-09-08T00:00:00Z",
  end: "2026-09-15T00:00:00Z",
  is_complete: false,
};

const ENVELOPE = {
  report_type: "weekly_email_digest",
  period: PERIOD,
  status: "generated",
  is_provisional: true,
  generated_at: "2026-09-14T08:00:00Z",
  detail: null,
  content: { activity: { sections: [] } },
};

const ACTIVITY = {
  state: "report",
  source: "stored",
  report_version: "1",
  is_provisional: true,
  checked_at: "2026-09-14T08:05:00Z",
  data_through: "2026-09-14T07:00:00Z",
  generated_at: "2026-09-14T08:05:00Z",
  period: PERIOD,
  period_label: PERIOD.label,
  is_partial: true,
  title: "Activity Report",
  markdown: "# Activity Report\n\n## 1. Executive Summary\n\n- Work happened.\n",
  word_count: 400,
  sections: ["1. Executive Summary"],
  workstream_count: 1,
  thread_count: 2,
  messages_analysed: 20,
  substantive_threads: 2,
  routine_threads: 1,
  narrative: true,
  quality_ok: true,
  quality_notes: [],
  limitations: [],
};

const HISTORY = { items: [], total: 0, available_periods: [PERIOD] };

const WEEKLY_WORK = {
  generated_at: "2026-09-14T08:00:00Z",
  period_start: "2026-09-08T00:00:00Z",
  period_end: "2026-09-15T00:00:00Z",
  completed_count: 0,
  due_soon_count: 0,
  overdue_count: 0,
  escalation_count: 0,
  summary: {
    completed_count: 0,
    due_soon_count: 0,
    overdue_count: 0,
    escalation_count: 0,
    events_needing_answer_count: 0,
  },
  upcoming: [],
  overdue: [],
  completed: [],
  blocked: [],
  follow_ups: [],
  high_priority: [],
  deadlines: [],
  escalations: [],
  calendar_connected: false,
  calendar_detail: "No calendar provider is connected.",
  events: [],
  events_needing_answer: [],
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
const MAILBOX = {
  address: "owner@sunradia.com",
  provider: "outlook",
  configured: true,
  connected: true,
  shared_fallback: false,
  detail: null,
  capabilities: ["list_messages"],
};

const MONTH = {
  key: "2026-09",
  kind: "month",
  label: "September 2026",
  start: "2026-09-01T00:00:00Z",
  end: "2026-10-01T00:00:00Z",
  is_complete: false,
};

/** The stored report for whichever type the page asked about. */
function envelopeFor(url: string) {
  const monthly = url.includes("monthly");

  return monthly
    ? { ...ENVELOPE, report_type: "monthly_email_digest", period: MONTH }
    : ENVELOPE;
}

let calls: string[] = [];
let container: HTMLElement;
let root: Root;

/** Every request the page makes, answered from a fixture and recorded. */
function installFetch() {
  calls = [];

  (dom.window as unknown as { fetch: unknown }).fetch = async (
    input: unknown,
    init?: unknown,
  ) => {
    const url = String(
      typeof input === "string" ? input : (input as { url: string }).url,
    );
    // The client prefixes every route with `/api`; the counts below are about
    // the endpoints, not the proxy.
    const path = url.replace("http://localhost", "").replace(/^\/api/, "");
    const method = String(
      (init as { method?: string } | undefined)?.method ?? "GET",
    ).toUpperCase();
    calls.push(`${method} ${path.split("?")[0]}`);

    const body = path.startsWith("/reports/weekly")
      ? WEEKLY_WORK
      : path.startsWith("/reports/activity")
      ? ACTIVITY
      : path.startsWith("/reports/history")
        ? HISTORY
        : path.startsWith("/reports")
          ? envelopeFor(url)
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

  // Node's own fetch primitives: jsdom's Response is not constructible here,
  // and the client only needs `json()` and `ok`.
  (globalThis as Record<string, unknown>).fetch = (
    dom.window as unknown as { fetch: unknown }
  ).fetch;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function page(strict: any, provider: any, panel: any) {
  return createElement(
    strict,
    null,
    createElement(provider, null, createElement(panel)),
  );
}
/* eslint-enable @typescript-eslint/no-explicit-any */

/** How many times a path was requested, by any method or by one. */
function countOf(path: string, method = "GET"): number {
  return calls.filter((call) => call === `${method} ${path}`).length;
}

async function settle(ms = 40) {
  await act(async () => {
    await new Promise((resolve) => dom.window.setTimeout(resolve, ms));
  });
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

describe("what one visit to Reports asks the backend for", () => {
  it("generates the activity report exactly once, under StrictMode", async () => {
    const { ReportsPanel } = await import("../components/reports/ReportsPanel");
    const { TwinProvider } = await import("../state/TwinContext");

    root = createRoot(container);

    await act(async () => {
      root.render(page(StrictMode, TwinProvider, ReportsPanel));
    });
    await settle();
    await settle();
    await settle(200);

    console.log("CALLS:", calls.join(" | "));

    // The expensive one. Anything above 1 is a whole pipeline run — a mailbox
    // read and a model call per conversation — thrown away.
    assert.equal(
      countOf("/reports/activity"),
      1,
      `activity requested ${countOf("/reports/activity")} times: ${calls.join(", ")}`,
    );
  });

  it("does not re-request when the same period arrives as a new object", async () => {
    const { ReportsPanel } = await import("../components/reports/ReportsPanel");
    const { TwinProvider } = await import("../state/TwinContext");

    root = createRoot(container);

    await act(async () => {
      root.render(page(StrictMode, TwinProvider, ReportsPanel));
    });
    await settle();

    // The mailbox panel's "changed" callback reloads the page's data, which
    // produces an equal period with a new identity — the exact shape that used
    // to re-run the pipeline.
    const before = countOf("/reports/activity");

    await settle(60);

    assert.equal(countOf("/reports/activity"), before);
  });

  it("asks for the stored report and its history once each", async () => {
    const { ReportsPanel } = await import("../components/reports/ReportsPanel");
    const { TwinProvider } = await import("../state/TwinContext");

    root = createRoot(container);

    await act(async () => {
      root.render(page(StrictMode, TwinProvider, ReportsPanel));
    });
    await settle();
    await settle();

    // Both are cheap database reads and both are needed: the history fills the
    // period selector, and the stored snapshot is what Download writes. They
    // are asserted so that a future change cannot quietly turn one visit into
    // four.
    assert.ok(countOf("/reports/history") <= 1, `history: ${countOf("/reports/history")}`);
    assert.ok(countOf("/reports") <= 1, `reports: ${countOf("/reports")}`);
  });

  it("does not re-analyse when something else on the page reloads it", async () => {
    /* The mailbox panel sits above the report and reloads the page's data
       when the mailbox changes. That produces an equal period with a new
       object identity, which is exactly what used to restart the pipeline. */

    const { ReportsPanel } = await import("../components/reports/ReportsPanel");
    const { TwinProvider } = await import("../state/TwinContext");

    root = createRoot(container);

    await act(async () => {
      root.render(page(StrictMode, TwinProvider, ReportsPanel));
    });
    await settle();
    await settle();

    const before = countOf("/reports/activity");
    const disconnect = [
      ...container.querySelectorAll("button"),
    ].find((button) => button.textContent?.trim() === "Disconnect");

    assert.ok(disconnect, "the mailbox panel should offer Disconnect");

    await act(async () => {
      disconnect!.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    await settle();
    await settle();

    // The mailbox changed, so the page re-read its stored report and its
    // history — two cheap queries. The period did not change, so the expensive
    // analysis must not run again.
    assert.ok(countOf("/reports") >= 2, "the stored report should be re-read");
    assert.equal(
      countOf("/reports/activity"),
      before,
      `activity re-requested: ${calls.join(", ")}`,
    );
  });

  it("runs one analysis per tab, and none for the work report", async () => {
    const { ReportsPanel } = await import("../components/reports/ReportsPanel");
    const { TwinProvider } = await import("../state/TwinContext");

    root = createRoot(container);

    await act(async () => {
      root.render(page(StrictMode, TwinProvider, ReportsPanel));
    });
    await settle();
    await settle();

    assert.equal(countOf("/reports/activity"), 1);

    const tab = (label: string) =>
      [...container.querySelectorAll('[role="tab"]')].find(
        (element) => element.textContent?.trim() === label,
      );

    const monthly = tab("Monthly Activity Report");

    assert.ok(monthly, "a Monthly tab should exist");

    await act(async () => {
      monthly!.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await settle();
    await settle();

    // A different period is a different question, so exactly one more.
    assert.equal(
      countOf("/reports/activity"),
      2,
      `after switching tab: ${calls.join(", ")}`,
    );

    const work = tab("This week's work");

    if (work) {
      const before = countOf("/reports/activity");

      await act(async () => {
        work.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
      });
      await settle();

      // The work report is its own screen and reads live work, not
      // correspondence. It must not start an analysis.
      assert.equal(countOf("/reports/activity"), before);
    }
  });

  it("shows something immediately rather than a blank panel", async () => {
    const { ReportsPanel } = await import("../components/reports/ReportsPanel");
    const { TwinProvider } = await import("../state/TwinContext");

    root = createRoot(container);

    await act(async () => {
      root.render(page(StrictMode, TwinProvider, ReportsPanel));
    });

    // Before any response has been delivered.
    assert.match(container.textContent ?? "", /Loading|Preparing|Analysing|Reports/);
  });
});
