import { describe, expect, it } from "vitest";

import { buildDelegation, periodInstants, validateAvailability, type AvailabilityForm } from "./availability-model";

const good: AvailabilityForm = { starts: "2026-10-20", ends: "2026-10-22", delegate: "d", reason: "Away", covers: "all" };

describe("out-of-office form", () => {
  it("converts dates to the first and last instants of the days in the browser zone (D-M2-14)", () => {
    const p = periodInstants("2026-10-20", "2026-10-22")!;
    const start = new Date(p.startsAt);
    const end = new Date(p.endsAt);
    expect([start.getFullYear(), start.getMonth(), start.getDate(), start.getHours(), start.getMinutes()]).toEqual([2026, 9, 20, 0, 0]);
    expect([end.getDate(), end.getHours(), end.getMinutes(), end.getSeconds()]).toEqual([22, 23, 59, 59]);
    expect(periodInstants("", "2026-10-22")).toBeNull();
  });

  it("reports every problem with a category", () => {
    const problems = validateAvailability({ ...good, starts: "", ends: "", delegate: "" });
    expect(problems.map((p) => p.field)).toEqual(["starts", "ends", "delegate"]);
    expect(validateAvailability({ ...good, ends: "2026-10-19" })[0]).toMatchObject({ category: "Dates", field: "ends" });
    expect(validateAvailability(good)).toEqual([]);
  });

  it("sends purchase orders only when chosen", () => {
    expect(buildDelegation(good).document_types).toBeUndefined();
    expect(buildDelegation({ ...good, covers: "po" }).document_types).toEqual(["po", "po_change"]);
  });
});
