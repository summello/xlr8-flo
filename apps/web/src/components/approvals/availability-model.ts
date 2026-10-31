import type { components } from "../../api/generated/schema";

export type AvailabilityForm = {
  starts: string;
  ends: string;
  delegate: string;
  reason: string;
  covers: "all" | "po";
};

export type Problem = {
  category: "Required field" | "Dates" | "Overlap" | "Not saved";
  field: "starts" | "ends" | "delegate" | "reason";
  message: string;
};

const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** D-M2-14: the first day starts at local midnight, the last day ends at its last millisecond. */
export function periodInstants(starts: string, ends: string): { startsAt: string; endsAt: string } | null {
  const a = DAY.exec(starts);
  const b = DAY.exec(ends);
  if (!a || !b) return null;
  const startsAt = new Date(Number(a[1]), Number(a[2]) - 1, Number(a[3]), 0, 0, 0, 0);
  const endsAt = new Date(Number(b[1]), Number(b[2]) - 1, Number(b[3]), 23, 59, 59, 999);
  return { startsAt: startsAt.toISOString(), endsAt: endsAt.toISOString() };
}

export function validateAvailability(form: AvailabilityForm): Problem[] {
  const problems: Problem[] = [];
  if (form.starts === "") problems.push({ category: "Required field", field: "starts", message: "Choose the first day you are away." });
  if (form.ends === "") problems.push({ category: "Required field", field: "ends", message: "Choose the last day you are away." });
  if (form.starts !== "" && form.ends !== "" && form.ends < form.starts) problems.push({ category: "Dates", field: "ends", message: "The last day cannot be before the first day." });
  if (form.delegate === "") problems.push({ category: "Required field", field: "delegate", message: "Choose who receives your approvals." });
  if (form.reason.trim() === "") problems.push({ category: "Required field", field: "reason", message: "Say why you are away." });
  return problems;
}

export function buildDelegation(form: AvailabilityForm): components["schemas"]["DelegationCreate"] {
  const period = periodInstants(form.starts, form.ends);
  if (period === null) throw new Error("Dates are validated before a delegation is built.");
  return {
    delegate_id: form.delegate,
    starts_at: period.startsAt,
    ends_at: period.endsAt,
    reason: form.reason.trim(),
    ...(form.covers === "po" ? { document_types: ["po" as const, "po_change" as const] } : {}),
  };
}
