import { describe, expect, it } from "vitest";
import { cancelConsequence, HOLDS, LIFECYCLE, FUTURE_STATES, TABS, sentSignal } from "./api";

describe("sentSignal", () => {
  it("maps each of the seven results to MASTER 2.3's word and tone, the date after the word", () => {
    const words = { queued: ["info", "Email Queued"], sent: ["info", "Email Sent"], delivered: ["success", "Email Delivered"], failed: ["danger", "Email Failed"],
      bounced: ["danger", "Email Bounced"], complained: ["danger", "Email Complaint"], address_suppressed: ["danger", "Address Suppressed"] } as const;
    for (const [result, [tone, label]] of Object.entries(words) as [keyof typeof words, readonly [string, string]][]) {
      expect(sentSignal({ result, result_at: null })).toEqual({ tone, label });
    }
    expect(sentSignal({ result: "delivered", result_at: "2026-10-12T10:00:00Z" })).toEqual({ tone: "success", label: expect.stringMatching(/^Email Delivered .*12/) });
  });
});

describe("purchase order vocabulary", () => {
  it("has the six server tabs and the three M4 states as future steps", () => {
    expect(TABS.map(t => t[0])).toEqual(["draft", "awaiting", "ready", "issued", "attention", "all"]);
    expect(FUTURE_STATES.every(s => LIFECYCLE.some(([status]) => status === s))).toBe(true);
  });
  it("names what the PO holds in each budget state", () => {
    expect(Object.keys(HOLDS).sort()).toEqual(["committed", "released", "reserved"]);
  });
});

describe("cancelConsequence", () => {
  const project = (code: string) => ({ id: code, code, name: `${code} name` });
  const base = { status: "issued" as const, released: "1234.5000", released_currency: "USD", rereserved: true, requisition_number: "REQ-9", reservation_project: project("SUB-1"), main_project: project("MAIN-1") };
  it("says nothing is released before issue, and that a withdrawal happens while approval is pending", () => {
    const draft = { ...base, status: "draft" as const, released: "0.0000", released_currency: null, rereserved: false, reservation_project: null };
    expect(cancelConsequence(draft, false)).toBe("Nothing was issued, so nothing is committed or released. The requisition keeps its reservation.");
    expect(cancelConsequence({ ...draft, status: "approval_pending" }, true)).toMatch(/The approval request is withdrawn\.$/);
  });
  it("says nothing is left when the issued commitment is used", () => {
    expect(cancelConsequence({ ...base, released: "0.0000", released_currency: null, rereserved: false, reservation_project: null }, false)).toBe("Nothing is left to release: the commitment is used.");
  });
  it("re-reserves on the reservation's project, or returns to the main project when the lines are cancelled", () => {
    expect(cancelConsequence(base, false)).toBe("Releases 1,234.50 USD once and re-reserves it for requisition REQ-9 on SUB-1 SUB-1 name, so another PO can order the reopened quantity without new approval.");
    expect(cancelConsequence(base, true)).toBe("Releases 1,234.50 USD once and returns it to MAIN-1 MAIN-1 name. The requisition lines are cancelled.");
  });
  it("says the money is available again where it was committed when no reservation is held", () => {
    const none = { ...base, rereserved: false, reservation_project: null };
    expect(cancelConsequence(none, false)).toBe("Releases 1,234.50 USD once. The requisition holds no reservation, so the money is available again on the project it was committed to.");
    expect(cancelConsequence(none, true)).toMatch(/ The requisition lines are cancelled\.$/);
  });
});
