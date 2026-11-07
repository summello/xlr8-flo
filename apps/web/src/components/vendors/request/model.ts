import type { components } from "../../../api/generated/schema";

export type Question = components["schemas"]["QuestionPublic"];
export type Answer = string | boolean | string[] | null;
export type ContactDraft = { name: string; email: string; phone: string; receives: boolean };
export type Problem = { category: string; message: string; target: string };
export type Form = {
  trading_name: string; legal_name: string;
  tax_id: string; tax_id_country: string; identifiers: Record<string, string>;
  availability: "global" | "restricted"; unit_ids: string[];
  contacts: { billing: ContactDraft; service_delivery: ContactDraft };
  answers: Record<string, Answer>;
  override_reason: string;
};

/** The server's default (VEN-002, decision 6h-8): Service and Delivery receives RFQs and POs, Billing does not. */
export const EMPTY: Form = {
  trading_name: "", legal_name: "", tax_id: "", tax_id_country: "", identifiers: {},
  availability: "global", unit_ids: [],
  contacts: {
    billing: { name: "", email: "", phone: "", receives: false },
    service_delivery: { name: "", email: "", phone: "", receives: true },
  },
  answers: {}, override_reason: "",
};

export const CONTACT_LABELS = { billing: "Billing", service_delivery: "Service and Delivery" } as const;
export type ContactType = keyof typeof CONTACT_LABELS;
export const CONTACT_TYPES = Object.keys(CONTACT_LABELS) as ContactType[];

const CATEGORY = { company: "Company", identifiers: "Identifiers", scope: "Where It Applies", contacts: "Contacts", answers: "Questionnaire", documents: "Documents" } as const;

/** `contacts.billing` -> `Contacts` and the control `vr-billing-name`; every control id starts `vr-`. */
export function problemFor(field: string, message: string): Problem {
  const parts = field.split(/[./]/), head = parts[0] ?? "", rest = parts.slice(1).join(".");
  if (head === "trading_name" || head === "legal_name") return { category: CATEGORY.company, message, target: `vr-${head}` };
  if (head === "tax_id" || head === "tax_id_country") return { category: CATEGORY.identifiers, message, target: head === "tax_id" ? "vr-tax_id" : "vr-tax_id_country" };
  if (head === "identifiers") return { category: CATEGORY.identifiers, message, target: rest ? `vr-id-${rest}` : "vr-tax_id" };
  if (head === "availability" || head === "unit_ids") return { category: CATEGORY.scope, message, target: head === "unit_ids" ? "vr-unit_ids" : "vr-availability" };
  if (head === "contacts") return { category: CATEGORY.contacts, message, target: `vr-${(rest || "billing").replace(".", "-")}${rest.includes(".") ? "" : "-name"}` };
  if (head === "answers") return { category: CATEGORY.answers, message, target: `vr-q-${rest}` };
  if (head === "documents") return { category: CATEGORY.documents, message, target: "vr-docs" };
  return { category: "Request", message, target: "vr-trading_name" };
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function hasAnswer(a: Answer | undefined): boolean {
  return a !== undefined && a !== null && a !== "" && !(Array.isArray(a) && a.length === 0);
}

/** The required fields only, for speed; the server repeats every rule (A11Y-006). Names alone are enough to save a draft. */
export function clientProblems(form: Form, questions: readonly Question[], mode: "save" | "submit"): Problem[] {
  const out: Problem[] = [];
  const need = (field: string, message: string) => out.push(problemFor(field, message));
  if (!form.trading_name.trim()) need("trading_name", "Enter the trading name");
  if (!form.legal_name.trim()) need("legal_name", "Enter the legal name");
  if (form.tax_id.trim() && !/^[A-Z]{2}$/.test(form.tax_id_country)) need("tax_id_country", "Choose the country that issued the tax id");
  if (mode === "save") return out;
  if (form.availability === "restricted" && form.unit_ids.length === 0) need("unit_ids", "Choose at least one unit");
  for (const type of CONTACT_TYPES) {
    const c = form.contacts[type], label = CONTACT_LABELS[type];
    if (!c.name.trim()) need(`contacts.${type}.name`, `Enter the ${label} contact name`);
    if (!c.email.trim()) need(`contacts.${type}.email`, `Enter the ${label} contact email`);
    else if (!EMAIL.test(c.email.trim())) need(`contacts.${type}.email`, `Enter a valid ${label} contact email`);
  }
  for (const q of questions) if (q.required && !hasAnswer(form.answers[q.key])) need(`answers.${q.key}`, `Answer "${q.prompt}"`);
  return out;
}

/** The MASTER 7.11 line under the action bar. */
export const complete = (form: Form, questions: readonly Question[]) => clientProblems(form, questions, "submit").length === 0;

/** What one duplicate-check match says, naming the vendor only when the server did. */
export function matchText(m: { vendor_number?: string | null; trading_name?: string | null }): string {
  return m.trading_name ? `Already used by ${m.trading_name}${m.vendor_number ? ` (${m.vendor_number})` : ""}.` : "Already used by another vendor.";
}

/** Query for `GET /vendors/duplicate-check`; identifiers ride as `identifiers[type]=value` keys. */
export function duplicateQuery(form: Form): Record<string, string> {
  const q: Record<string, string> = {};
  if (form.tax_id.trim()) { q.tax_id = form.tax_id.trim(); if (form.tax_id_country.length === 2) q.tax_id_country = form.tax_id_country; }
  for (const [type, value] of Object.entries(form.identifiers)) if (value.trim()) q[`identifiers[${type}]`] = value.trim();
  return q;
}
