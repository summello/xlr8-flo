import { describe, expect, it } from "vitest";
import { EMPTY, clientProblems, complete, duplicateQuery, matchText, problemFor, type Form, type Question } from "./model";

const q = (key: string, required: boolean): Question => ({ key, position: 1, prompt: `Prompt ${key}`, type: "text", required, choices: null });
const filled: Form = { ...EMPTY, trading_name: "Acme", legal_name: "Acme Ltd", contacts: {
  billing: { name: "Bea", email: "bea@example.test", phone: "", receives: false },
  service_delivery: { name: "Sam", email: "sam@example.test", phone: "", receives: true } } };

describe("request form model", () => {
  it("saves a draft with names alone", () => {
    expect(clientProblems(EMPTY, [], "save").map(p => p.target)).toEqual(["vr-trading_name", "vr-legal_name"]);
    expect(clientProblems({ ...EMPTY, trading_name: "A", legal_name: "B" }, [], "save")).toEqual([]);
  });
  it("submit needs both contacts, units when restricted and required answers", () => {
    const targets = clientProblems({ ...filled, availability: "restricted", contacts: EMPTY.contacts }, [q("a", true), q("b", false)], "submit").map(p => p.target);
    expect(targets).toEqual(["vr-unit_ids", "vr-billing-name", "vr-billing-email", "vr-service_delivery-name", "vr-service_delivery-email", "vr-q-a"]);
    expect(complete(filled, [q("a", false)])).toBe(true);
    expect(complete(filled, [q("a", true)])).toBe(false);
  });
  it("maps server field paths to the control that owns them", () => {
    expect(problemFor("contacts.billing", "x").target).toBe("vr-billing-name");
    expect(problemFor("contacts.service_delivery.email", "x").target).toBe("vr-service_delivery-email");
    expect(problemFor("answers.size", "x")).toMatchObject({ category: "Questionnaire", target: "vr-q-size" });
    expect(problemFor("identifiers.duns", "x").target).toBe("vr-id-duns");
    expect(problemFor("tax_id", "x").category).toBe("Identifiers");
  });
  it("names a duplicate vendor only when the server did, and sends identifiers as brackets", () => {
    expect(matchText({})).toBe("Already used by another vendor.");
    expect(matchText({ trading_name: "Old Co", vendor_number: "VEN-0007" })).toBe("Already used by Old Co (VEN-0007).");
    expect(duplicateQuery({ ...filled, tax_id: " 12-3 ", tax_id_country: "US", identifiers: { duns: "99" } })).toEqual({ tax_id: "12-3", tax_id_country: "US", "identifiers[duns]": "99" });
  });
});
