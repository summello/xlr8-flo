import { describe, expect, it } from "vitest";
import { instantIn, relativeDeadline, wallClock } from "./api";

describe("deadline helpers", () => {
  it("reads a wall clock in the RFQ's zone and maps it back to the same instant", () => {
    const iso = "2026-10-23T12:30:00.000Z";
    for (const zone of ["UTC", "Europe/London", "America/New_York", "Asia/Kolkata"]) {
      const { date, time } = wallClock(iso, zone);
      expect(instantIn(date, time, zone)?.toISOString()).toBe(iso);
    }
    expect(wallClock(iso, "Asia/Kolkata")).toEqual({ date: "2026-10-23", time: "18:00" });
  });
  it("refuses malformed dates and times", () => {
    expect(instantIn("2026-02-30", "10:00", "UTC")).toBeNull();
    expect(instantIn("2026-10-23", "24:00", "UTC")).toBeNull();
    expect(instantIn("23/10/2026", "10:00", "UTC")).toBeNull();
  });
  it("refuses a wall time the clocks skip and settles a repeated one", () => {
    expect(instantIn("2026-03-29", "01:30", "Europe/London")).toBeNull(); // clocks go 01:00 to 02:00
    expect(instantIn("2026-03-29", "02:30", "Europe/London")?.toISOString()).toBe("2026-03-29T01:30:00.000Z");
    expect(instantIn("2026-10-25", "01:30", "Europe/London")?.toISOString()).toBe("2026-10-25T01:30:00.000Z"); // 01:30 happens twice (BST then GMT); the later instant wins
  });
  it("says in or closed ... ago", () => {
    const now = Date.parse("2026-10-23T00:00:00Z");
    expect(relativeDeadline("2026-10-25T00:00:00Z", now)).toBe("in 2 days");
    expect(relativeDeadline("2026-10-20T00:00:00Z", now)).toBe("closed 3 days ago");
  });
});
