import { describe, expect, it } from "vitest";

import { nextRow, overdueLabel, parseTab, statusTone, stepLabel, subline, type InboxRow } from "./inbox-model";

const NOW = Date.parse("2026-10-13T09:00:00Z");

describe("inbox row mapping", () => {
  it("words overdue days and never colours alone", () => {
    expect(overdueLabel("2026-10-10T09:00:00Z", NOW)).toBe("3 days overdue");
    expect(overdueLabel("2026-10-12T08:00:00Z", NOW)).toBe("1 day overdue");
    expect(overdueLabel("2026-10-13T08:59:00Z", NOW)).toBe("1 day overdue");
    expect(overdueLabel("2026-10-14T09:00:00Z", NOW)).toBeNull();
    expect(overdueLabel(null, NOW)).toBeNull();
  });

  it("falls back to pending for an unknown or missing tab", () => {
    expect(parseTab("?tab=completed")).toBe("completed");
    expect(parseTab("?tab=nope")).toBe("pending");
    expect(parseTab("")).toBe("pending");
  });

  it("maps server status words to tones and steps to words", () => {
    expect(statusTone("Overdue")).toBe("danger");
    expect(statusTone("Approved")).toBe("success");
    expect(statusTone("Returned for Changes")).toBe("warning");
    expect(statusTone("With a@example.test")).toBe("neutral");
    expect(statusTone("Your Turn")).toBe("info");
    expect(stepLabel({ step: { index: 1, of: 2 } })).toBe("1 of 2");
  });

  it("picks the next row other than the one just decided", () => {
    const rows = [{ task_id: "a" }, { task_id: "b" }] as InboxRow[];
    expect(nextRow(rows, "a")?.task_id).toBe("b");
    expect(nextRow([rows[0]!], "a")).toBeUndefined();
  });

  it("words the server counts without adding any money", () => {
    const counts = { pending: 9, overdue: 2, completed: 0, delegated: 0 };
    expect(subline(counts, [{ amount: "1.0000", currency: "USD" }])).toBe("9 waiting on you, 2 overdue · amounts in USD");
    expect(subline(counts, [])).toBe("9 waiting on you, 2 overdue");
  });
});
