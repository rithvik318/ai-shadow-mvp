import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiError } from "../api/client";
import { fallbackFor, faultMessage, isConfigurationGap } from "../lib/reportFault";

/**
 * The rule these tests pin is the one that let a defect hide.
 *
 * `GET /reports/activity` returned HTTP 500 on every request from a user with
 * a mailbox connected. The Reports screen showed the stored email digest
 * whenever the detailed report was absent — for any reason — so the screen
 * read "83 messages received, 0 tasks completed" with a small error line above
 * it, and looked like a working report with a transient hiccup.
 *
 * A fallback is therefore allowed for exactly one reason and no others.
 */
describe("when the stored digest may stand in for the detailed report", () => {
  it("falls back when no mailbox is connected", () => {
    const fault = new ApiError(
      "No mailbox is connected for this user.",
      409,
      "EmailProviderNotConfiguredError",
    );

    assert.equal(isConfigurationGap(fault), true);
    assert.equal(fallbackFor(fault), "digest");
  });

  it("never falls back on a server error", () => {
    const fault = new ApiError("Internal Server Error", 500, "AttributeError");

    assert.equal(isConfigurationGap(fault), false);
    assert.equal(fallbackFor(fault), "error");
  });

  it("never falls back when the mailbox itself refused", () => {
    const fault = new ApiError(
      "Microsoft Graph refused the credentials.",
      502,
      "EmailProviderAuthError",
    );

    assert.equal(fallbackFor(fault), "error");
  });

  it("does not let an unrelated 409 inherit the fallback", () => {
    const fault = new ApiError("Already exists", 409, "DuplicateUserError");

    assert.equal(fallbackFor(fault), "error");
  });

  it("treats a network failure as an error, not a quiet month", () => {
    assert.equal(fallbackFor(new Error("Failed to fetch")), "error");
  });

  it("shows nothing at all when there is no failure", () => {
    assert.equal(fallbackFor(null), null);
  });
});

describe("what the reader is told", () => {
  it("attributes a server fault and keeps its status visible", () => {
    const message = faultMessage(
      new ApiError("Internal Server Error", 500, "AttributeError"),
    );

    assert.match(message, /HTTP 500/);
    assert.match(message, /server/i);
  });

  it("passes a configuration message through as written", () => {
    const message = faultMessage(
      new ApiError(
        "No mailbox is connected for this user.",
        409,
        "EmailProviderNotConfiguredError",
      ),
    );

    assert.equal(message, "No mailbox is connected for this user.");
  });

  it("never renders an empty notice", () => {
    assert.ok(faultMessage(new Error("")).length > 0);
  });
});
