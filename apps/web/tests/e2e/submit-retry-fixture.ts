import type { Page } from "@playwright/test";

export type Step = "abort" | "pass" | { status: number; body: unknown };

/** Scripts the answers to POSTs on one URL (the stub answers "pass"): returns the Idempotency-Key of every attempt, in order. */
export async function scripted(page: Page, url: RegExp, steps: Step[]) {
  const keys: (string | undefined)[] = [];
  await page.route(url, async route => {
    if (route.request().method() !== "POST") return route.fallback();
    keys.push(route.request().headers()["idempotency-key"]);
    const step = steps[keys.length - 1] ?? "pass";
    if (step === "abort") return route.abort();
    if (step === "pass") return route.fallback();
    return route.fulfill({ status: step.status, contentType: "application/json", body: JSON.stringify(step.body) });
  });
  return keys;
}

export const refusal = { status: 409, body: { type: "about:blank", title: "Conflict", status: 409, detail: "Refused." } } as const;
export const inFlight = { status: 409, body: { type: "https://xlr8flo.summello.com/problems/request_in_flight", title: "Conflict", status: 409, detail: "Still running." } } as const;
