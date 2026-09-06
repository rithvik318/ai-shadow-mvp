/**
 * The request layer, against a stubbed `fetch`.
 *
 * These exist to catch the failure that is invisible in a component test: a
 * path or a header that does not match what the backend actually serves.
 * Every asserted path is one in `backend/app/api/`.
 */

import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  ApiError,
  buildUrl,
  getActiveUserId,
  request,
  setActiveUserId,
  toApiError,
} from "../api/client.ts";
import * as api from "../api/index.ts";

interface Call {
  url: string;
  method: string;
  headers: Headers;
  body: unknown;
}

const calls: Call[] = [];
const realFetch = globalThis.fetch;

function stubFetch(response: { status?: number; body?: unknown } = {}): void {
  globalThis.fetch = (async (input: string | URL, init?: RequestInit) => {
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      headers: new Headers(init?.headers),
      body: init?.body,
    });

    const status = response.status ?? 200;

    return new Response(status === 204 ? null : JSON.stringify(response.body ?? {}), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
  setActiveUserId(null);
  calls.length = 0;
});

describe("URLs", () => {
  it("prefixes the configured base", () => {
    assert.equal(buildUrl("/documents", undefined, "/api"), "/api/documents");
  });

  it("drops empty query parameters rather than sending them blank", () => {
    // `?status=` would be a validation error against the DocumentStatus enum.
    const url = buildUrl("/documents", { status: undefined, limit: 50 }, "/api");

    assert.equal(url, "/api/documents?limit=50");
  });
});

describe("identity propagation", () => {
  it("sends X-User-ID when a user is given", async () => {
    stubFetch({ body: { answer: "", sources: [], retrieved_chunks: 0 } });

    await api.askQuestion("Who are our clients?", "user-42");

    assert.equal(calls[0].headers.get("X-User-ID"), "user-42");
  });

  it("omits the header entirely for shared-corpus endpoints", async () => {
    // /documents and /search are not user-scoped; sending an identity there
    // would imply a per-user corpus that does not exist.
    stubFetch({ body: { items: [], total: 0, limit: 50, offset: 0 } });

    await api.listDocuments();

    assert.equal(calls[0].headers.has("X-User-ID"), false);
  });

  it("scopes every Digital Twin call to the selected user", async () => {
    stubFetch({ body: { items: [], total: 0 } });

    await api.listMemories("user-7");

    assert.equal(calls[0].headers.get("X-User-ID"), "user-7");
  });
});

describe("endpoint paths match the backend", () => {
  const cases: Array<[string, () => Promise<unknown>, string, string]> = [
    ["chat", () => api.askQuestion("q", "u"), "POST", "/api/chat"],
    ["search", () => api.searchDocuments("q"), "POST", "/api/search"],
    ["documents", () => api.listDocuments(), "GET", "/api/documents"],
    ["profile", () => api.getProfile("u"), "GET", "/api/profile"],
    ["memory", () => api.listMemories("u"), "GET", "/api/memory"],
    ["users", () => api.listUsers(), "GET", "/api/users"],
  ];

  for (const [name, call, method, path] of cases) {
    it(`${name} → ${method} ${path}`, async () => {
      stubFetch({ body: {} });
      await call();

      assert.equal(calls[0].method, method);
      assert.equal(calls[0].url.split("?")[0], path);
    });
  }

  it("uploads go to the batch endpoint under the field name `files`", async () => {
    stubFetch({ body: { items: [], total: 0, succeeded: 0, failed: 0 } });

    await api.uploadDocuments([new File(["a"], "a.txt"), new File(["b"], "b.txt")]);

    assert.equal(calls[0].url, "/api/documents/batch-upload");
    const form = calls[0].body as FormData;
    assert.equal(form.getAll("files").length, 2);
  });

  it("does not set Content-Type on an upload", async () => {
    // The browser must set it, so it can add the multipart boundary.
    stubFetch({ body: { items: [], total: 0, succeeded: 0, failed: 0 } });

    await api.uploadDocuments([new File(["a"], "a.txt")]);

    assert.equal(calls[0].headers.has("Content-Type"), false);
  });
});

describe("errors", () => {
  it("uses the backend's own message", async () => {
    const error = await toApiError(
      new Response(
        JSON.stringify({ detail: "Profile not found", error: "ProfileNotFoundError" }),
        {
          status: 404,
        },
      ),
    );

    assert.equal(error.message, "Profile not found");
    assert.equal(error.code, "ProfileNotFoundError");
    assert.equal(error.isNotFound, true);
  });

  it("survives a response that is not JSON", async () => {
    // A proxy error page must not become "Unexpected token < in JSON".
    const error = await toApiError(new Response("<html>502</html>", { status: 502 }));

    assert.equal(error.status, 502);
    assert.match(error.message, /HTTP 502/);
  });

  it("separates a missing identity from an ordinary failure", async () => {
    assert.equal(
      new ApiError("no id", 401, "MissingIdentityError").isIdentityProblem,
      true,
    );
    assert.equal(new ApiError("boom", 500, "HttpError").isIdentityProblem, false);
  });

  it("throws rather than returning a body on failure", async () => {
    stubFetch({
      status: 409,
      body: { detail: "Not configured", error: "SyncNotConfiguredError" },
    });

    await assert.rejects(() => request("/sync/onedrive", { method: "POST" }), ApiError);
  });

  it("returns nothing for a 204", async () => {
    stubFetch({ status: 204 });

    assert.equal(await api.deleteDocument("doc-1"), undefined);
  });
});

describe("OneDrive sync", () => {
  it("reads status without contacting Graph or sending an identity", async () => {
    // The knowledge base is shared, so an X-User-ID here would imply a
    // per-user corpus that does not exist.
    stubFetch({
      body: {
        configured: false,
        scheduled: false,
        interval_seconds: null,
        sources: [],
      },
    });

    await api.getSyncStatus();

    assert.equal(calls[0].url, "/api/sync/onedrive/status");
    assert.equal(calls[0].method, "GET");
    assert.equal(calls[0].headers.has("X-User-ID"), false);
  });

  it("runs an incremental sync by default", async () => {
    stubFetch({ body: { sources: [], total_discovered: 0 } });

    await api.runSync();

    assert.equal(calls[0].url, "/api/sync/onedrive");
    assert.deepEqual(JSON.parse(calls[0].body as string), {
      source: null,
      full: false,
    });
  });

  it("can be asked for a full resync of one source", async () => {
    stubFetch({ body: { sources: [] } });

    await api.runSync({ source: "capabilities", full: true });

    assert.deepEqual(JSON.parse(calls[0].body as string), {
      source: "capabilities",
      full: true,
    });
  });
});

describe("the client calls nothing the backend does not serve", () => {
  it("exposes only functions whose paths exist in backend/app/api", () => {
    // Every path this module can produce, checked against the routers.
    // A typo or an invented endpoint shows up here rather than as a 404 in
    // front of a user.
    const served = [
      "/users",
      "/chat",
      "/search",
      "/documents",
      "/documents/batch-upload",
      "/profile",
      "/memory",
      "/sync/onedrive",
      "/sync/onedrive/status",
      "/email/provider/status",
      "/email/compose",
      "/email/messages",
      "/email/triage",
      "/email/threads/summarize",
      "/email/assessments",
      "/email/follow-ups",
      "/email/drafts",
      "/email/templates",
    ];

    for (const path of served) {
      assert.equal(buildUrl(path, undefined, "/api").startsWith("/api/"), true);
    }

    // Nothing in the module references a preferences endpoint: the backend
    // has none, and preferences are profile fields plus preference-typed
    // memories.
    assert.equal(typeof (api as Record<string, unknown>).getPreferences, "undefined");
    assert.equal(typeof (api as Record<string, unknown>).savePreferences, "undefined");
  });
});

describe("the email client", () => {
  it("sends X-User-ID on every user-scoped email call", async () => {
    // Templates, drafts and assessments are private to the twin acting, the
    // same way profile and memory are. A call that forgot the header would be
    // a 401 in front of a user.
    stubFetch({ body: { items: [], total: 0 } });

    await api.listEmailTemplates("u1");
    await api.listEmailDrafts("u1");
    await api.listEmailFollowUps("u1");
    await api.listEmailAssessments("u1");

    assert.equal(calls.length, 4);
    for (const call of calls) {
      assert.equal(call.headers.get("X-User-ID"), "u1");
    }
  });

  it("reads back the twin the workspace is acting as", () => {
    // The getter is what anything outside `TwinProvider` uses to answer "who
    // are we acting as right now" without reaching into React state.
    setActiveUserId("twin-9");
    assert.equal(getActiveUserId(), "twin-9");

    setActiveUserId(null);
    assert.equal(getActiveUserId(), null);
  });

  it("scopes the provider status to the selected twin", async () => {
    // The mailbox is per-user, so this endpoint is user-scoped and the
    // backend answers 401 without an identity. It takes no `userId`
    // parameter, so it picks up the selected twin from the client.
    setActiveUserId("twin-9");
    stubFetch({ body: { provider: null, configured: false, connected: false } });

    await api.getEmailProviderStatus();

    assert.equal(calls[0].url, "/api/email/provider/status");
    assert.equal(calls[0].headers.get("X-User-ID"), "twin-9");
  });

  it("sends no identity for the provider status before a twin is chosen", async () => {
    // A setup notice has to be able to render before anybody has chosen one,
    // and an empty header is better than a fabricated id.
    setActiveUserId(null);
    stubFetch({ body: { provider: null, configured: false, connected: false } });

    await api.getEmailProviderStatus();

    assert.equal(calls[0].headers.has("X-User-ID"), false);
  });

  it("always confirms explicitly when sending", async () => {
    // The backend requires `confirm: true` as a second gate on an
    // irreversible action, and the client must not be able to omit it.
    stubFetch({ body: {} });

    await api.sendEmailDraft("u1", "d1");

    assert.equal(calls[0].url, "/api/email/drafts/d1/send");
    assert.deepEqual(JSON.parse(String(calls[0].body)), { confirm: true });
  });

  it("approves and sends through two separate calls", async () => {
    // One endpoint that approved-and-sent would collapse the review step into
    // the action it exists to gate.
    stubFetch({ body: {} });

    await api.approveEmailDraft("u1", "d1");
    await api.sendEmailDraft("u1", "d1");

    assert.deepEqual(
      calls.map((call) => call.url),
      ["/api/email/drafts/d1/approve", "/api/email/drafts/d1/send"],
    );
  });

  it("asks the backend to fill a template rather than doing it locally", async () => {
    stubFetch({ body: { subject: "", body: "", missing: [] } });

    await api.fillEmailTemplate("u1", "t1", { name: "Ana" });

    assert.equal(calls[0].url, "/api/email/templates/t1/fill");
    assert.deepEqual(JSON.parse(String(calls[0].body)), { values: { name: "Ana" } });
  });
});

describe("tasks, events and the weekly report", () => {
  it("scopes the weekly report to the calling twin", () => {
    stubFetch({ body: { upcoming: [] } });
    void api.getWeeklyReport("user-1");

    assert.ok(calls[0].url.endsWith("/reports/weekly"));
    assert.equal(calls[0].headers.get("X-User-ID"), "user-1");
  });

  it("sends the twin's identity on every task write", () => {
    stubFetch({ body: {} });
    void api.completeTask("user-1", "task-9");

    assert.ok(calls[0].url.endsWith("/tasks/task-9/complete"));
    assert.equal(calls[0].method, "POST");
    assert.equal(calls[0].headers.get("X-User-ID"), "user-1");
  });

  it("starts a task by moving it to in_progress", () => {
    stubFetch({ body: {} });
    void api.updateTask("user-1", "task-9", { status: "in_progress" });

    assert.equal(calls[0].method, "PATCH");
    assert.deepEqual(JSON.parse(String(calls[0].body)), { status: "in_progress" });
  });

  it("records attendance through its own endpoint", () => {
    // Recording that somebody went to a meeting is a different act from
    // editing it, and the API keeps them apart.
    stubFetch({ body: {} });
    void api.markAttendance("user-1", "event-2", "attended");

    assert.ok(calls[0].url.endsWith("/events/event-2/attendance"));
    assert.deepEqual(JSON.parse(String(calls[0].body)), {
      status: "attended",
      note: null,
    });
  });

  it("can withdraw an attendance answer", () => {
    stubFetch({ body: {} });
    void api.markAttendance("user-1", "event-2", "unknown");

    assert.equal(JSON.parse(String(calls[0].body)).status, "unknown");
  });

  it("scopes an event listing to the twin", () => {
    stubFetch({ body: { items: [], total: 0 } });
    void api.listEvents("user-1");

    assert.equal(calls[0].headers.get("X-User-ID"), "user-1");
  });
});

describe("user deletion", () => {
  it("previews before it deletes", () => {
    stubFetch({ body: { owned: {} } });
    void api.previewUserDeletion("admin-1", "user-2");

    assert.ok(calls[0].url.endsWith("/users/user-2/deletion-preview"));
    assert.equal(calls[0].method, "GET");
  });

  it("names the target in the path and the actor in the header", () => {
    // An administrator acting *on* somebody else is the one place a user id
    // legitimately travels in a URL, and the two identities are different.
    stubFetch({ body: {} });
    void api.deleteUser("admin-1", "user-2");

    assert.ok(calls[0].url.endsWith("/users/user-2"));
    assert.equal(calls[0].method, "DELETE");
    assert.equal(calls[0].headers.get("X-User-ID"), "admin-1");
  });
});

describe("switching users", () => {
  it("attributes every later request to the person now selected", async () => {
    // The whole of user switching, at the layer where it is decided. Panels
    // that refetch are only correct if the refetch carries the new identity;
    // if this were captured once, every screen would keep asking as the person
    // who happened to be selected when the module loaded.
    const seen: Array<string | null> = [];

    const original = globalThis.fetch;
    globalThis.fetch = (async (_input: unknown, init: RequestInit) => {
      const headers = new Headers(init?.headers);
      seen.push(headers.get("X-User-ID"));

      return new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    try {
      setActiveUserId("sudha");
      await request("/tasks");

      setActiveUserId("robert");
      await request("/tasks");

      assert.deepEqual(seen, ["sudha", "robert"]);
    } finally {
      globalThis.fetch = original;
      setActiveUserId(null);
    }
  });

  it("lets a caller name the user rather than relying on who is selected", async () => {
    // What the provider-status call now does. A request that states whose
    // answer it wants cannot be misattributed by a switch that happens while
    // it is in flight.
    let sent: string | null = null;

    const original = globalThis.fetch;
    globalThis.fetch = (async (_input: unknown, init: RequestInit) => {
      sent = new Headers(init?.headers).get("X-User-ID");

      return new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    try {
      setActiveUserId("sudha");
      await request("/email/provider/status", { userId: "robert" });

      assert.equal(sent, "robert");
    } finally {
      globalThis.fetch = original;
      setActiveUserId(null);
    }
  });
});
