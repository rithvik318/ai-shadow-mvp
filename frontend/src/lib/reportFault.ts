import { ApiError } from "../api/client";

/**
 * Why the detailed activity report failed, and therefore what may be shown.
 *
 * This is a separate module because it is a rule, not a rendering detail, and
 * because the rendering it governs is what hid a real defect. `GET
 * /reports/activity` answered HTTP 500 — a reference to a setting that did not
 * exist — and the Reports screen, which fell back to the stored email digest
 * whenever the detailed report was absent, rendered "83 messages received" and
 * "0 tasks completed" underneath the error. It looked like a report. The 500
 * survived that way until somebody read the network tab.
 *
 * So the fallback is keyed on the reason, not on the absence:
 *
 * - **A mailbox that is not connected** is a configuration state. The server
 *   answered correctly and there is no correspondence to read, so the stored
 *   digest stands in and says that it is doing so.
 * - **Anything else** is a fault — a 500 from a programming error, a 502 from
 *   a mailbox that refused — and nothing stands in for it. The error is the
 *   content until the request succeeds.
 */
export type ActivityFallback = "digest" | "error";

/** The backend exception that means "no mailbox", mapped to 409 in `main.py`. */
export const NO_MAILBOX_CODE = "EmailProviderNotConfiguredError";

export function isConfigurationGap(fault: Error): boolean {
  // Both the status and the code: a future 409 on this endpoint meaning
  // something else must not inherit permission to be papered over.
  return (
    fault instanceof ApiError &&
    fault.status === 409 &&
    fault.code === NO_MAILBOX_CODE
  );
}

export function fallbackFor(fault: Error | null): ActivityFallback | null {
  if (!fault) return null;

  return isConfigurationGap(fault) ? "digest" : "error";
}

/**
 * What to put in front of a person.
 *
 * A server fault is attributed and numbered. "Something went wrong, try again"
 * invites somebody to retry a bug for ever, and gives them nothing to report.
 */
export function faultMessage(fault: Error): string {
  if (fault instanceof ApiError && fault.status >= 500) {
    return `The server failed to generate this report (HTTP ${fault.status}). ${fault.message}`;
  }

  return fault.message || "The detailed report could not be generated.";
}
