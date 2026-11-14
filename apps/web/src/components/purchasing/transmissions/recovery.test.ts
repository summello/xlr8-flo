import { describe, expect, it } from "vitest";
import type { PoTransmission } from "../api";
import { CANCEL_EMAIL, currentSend, ISSUE_EMAIL, recovery } from "./recovery";

const r = (...result: ("failed" | "bounced" | "address_suppressed" | "complained" | "delivered" | "queued")[]) => result.map(x => ({ result: x }));

describe("recovery", () => {
  it("is null while nothing failed", () => {
    expect(recovery(r("queued", "delivered"), true)).toBeNull();
  });
  it("complained, vendor visible: Change Recipient only, focused (decision 3)", () => {
    expect(recovery(r("complained", "delivered"), true)).toEqual({ actions: ["change_recipient"], focus: "change_recipient" });
  });
  it("complained, vendor hidden: no action, the message takes focus", () => {
    expect(recovery(r("complained"), false)).toEqual({ actions: [], focus: "message" });
  });
  it("bounced or suppressed, vendor visible: Change Recipient first and focused, Resend second", () => {
    for (const x of ["bounced", "address_suppressed"] as const) expect(recovery(r(x), true)).toEqual({ actions: ["change_recipient", "resend"], focus: "change_recipient" });
  });
  it("bounced or suppressed, vendor hidden: Resend only, focused", () => {
    expect(recovery(r("bounced"), false)).toEqual({ actions: ["resend"], focus: "resend" });
    expect(recovery(r("address_suppressed"), false)).toEqual({ actions: ["resend"], focus: "resend" });
  });
  it("only failed: Resend first and focused, Change Recipient second when allowed", () => {
    expect(recovery(r("failed"), true)).toEqual({ actions: ["resend", "change_recipient"], focus: "resend" });
    expect(recovery(r("failed"), false)).toEqual({ actions: ["resend"], focus: "resend" });
  });
  it("complained outranks bounced in the same send", () => {
    expect(recovery(r("bounced", "complained"), true)?.actions).toEqual(["change_recipient"]);
  });
});

describe("currentSend", () => {
  const t = (id: string, po_id: string, template_version: string, attempt: number) => ({ id, po_id, template_version, attempt }) as PoTransmission;
  const rows = [t("a", "po", ISSUE_EMAIL, 1), t("b", "po", ISSUE_EMAIL, 2), t("c", "po", CANCEL_EMAIL, 3), t("d", "po", CANCEL_EMAIL, 3), t("e", "old", CANCEL_EMAIL, 9)];
  it("defaults to the issue email, greatest attempt of this PO", () => {
    expect(currentSend(rows, "po").map(x => x.id)).toEqual(["b"]);
  });
  it("takes the cancellation notice's greatest attempt when asked, never another revision's", () => {
    expect(currentSend(rows, "po", CANCEL_EMAIL).map(x => x.id)).toEqual(["c", "d"]);
  });
});
