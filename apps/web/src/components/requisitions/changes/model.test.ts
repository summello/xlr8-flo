import { describe, expect, it } from "vitest";
import { baseRevision, CHANGE_LABEL, OUTCOME_LABEL, classify, consequence, deltaText, latestRevision, outcomeLabel, type ChangeRow, type ChangeSet } from "./model";

const row = (patch: Partial<ChangeRow> = {}): ChangeRow => ({ change: "changed", field: "lines/1/quantity", label: "Line 1 quantity", was: "10.0000", now: "9.0000", ...patch });
const set = (patch: Partial<ChangeSet> = {}): ChangeSet => ({ from_revision: 1, funding: null, material: true, nothing_changed: false, routing: "restarts", rows: [], to: "draft", ...patch });

describe("change set mapping", () => {
  it("shows the server delta string verbatim and never derives one from was and now", () => {
    // was 10 and now 9 would make a browser say "Down 1.00"; the server said 3,500.00 and 2%.
    expect(deltaText(row({ delta: { amount: "3500.0000", direction: "down", percent: "2" } }), "USD")).toBe("Down 3,500.00 USD (2%)");
    expect(deltaText(row(), "USD")).toBeNull();
    expect(deltaText(row({ delta: { amount: "62000.0000", direction: "up", percent: null } }), "USD")).toBe("Up 62,000.00 USD");
  });
  it("labels every funding outcome the server can send, and passes an unknown word through raw", () => {
    expect(Object.keys(OUTCOME_LABEL).sort()).toEqual(["moved", "released_once", "reserved_more", "unchanged"]);
    expect(Object.values(OUTCOME_LABEL)).toEqual(["Unchanged", "Released Once", "Reserved More", "Moved"]);
    expect(outcomeLabel("something_new")).toBe("something_new");
    expect(Object.values(CHANGE_LABEL)).toEqual(["Changed", "Added", "Removed"]);
  });
  it("reads the consequence from the server's routing, not from the rows", () => {
    expect(consequence(set({ routing: "same_step", material: false, rows: [row()] })).heading).toBe("Not a Material Change");
    expect(consequence(set({ routing: "restarts", material: true, rows: [] })).text).toMatch(/restarts from the first step/);
    expect(consequence(set({ routing: "not_applicable", material: true, to: "amendment" })).text).toMatch(/sent for approval/);
    expect(consequence(set({ routing: "not_applicable", material: false, to: "amendment" })).text).toMatch(/applied/);
  });
  it("sorts a failed submit into the four alerts", () => {
    expect(classify({ checks: { problem: "nothing_changed" } }, "resubmitted")).toMatchObject({ kind: "nothing_changed", message: "Not resubmitted. Nothing has changed." });
    expect(classify({ checks: { problem: "stale_version" } }, "resubmitted").kind).toBe("stale_version");
    const invalid = classify({ checks: { problem: "submission_invalid" }, errors: [{ field: "funds/estimate", message: "Short by 62,000.00 USD" }] }, "resubmitted");
    expect(invalid).toMatchObject({ kind: "submission_invalid", problems: [{ category: "Funds", target: "funding-check" }] });
    expect(classify({ detail: "Down." }, "submitted").kind).toBe("other");
  });
  it("names the newest version", () => {
    expect(latestRevision([{ revision_no: 1 }, { revision_no: 3 }, { revision_no: 2 }] as never, 1)).toBe(3);
    expect(latestRevision(null, 2)).toBe(2);
  });
  it("compares an amendment with the applied version it was opened on, not a later one", () => {
    const r = (revision_no: number, status: string) => ({ revision_no, status }) as never;
    expect(baseRevision([r(1, "applied"), r(2, "returned"), r(3, "applied"), r(4, "draft")], 4)).toBe(3);
    expect(baseRevision([r(1, "applied"), r(2, "applied"), r(3, "draft")], 2)).toBe(1);
    expect(baseRevision([r(1, "draft")], 1)).toBeUndefined();
  });
});
